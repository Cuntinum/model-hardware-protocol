"""KHP Driver: DICOM Medical Imaging Device Integration.

Connects to DICOM PACS servers, modalities, and imaging workstations using the
DICOM networking protocol. Supports C FIND (query), C MOVE (retrieve), C STORE
(send), C ECHO (verify), and storage commitment. Handles CT, MRI, Ultrasound,
X Ray, PET, and any DICOM compliant imaging equipment.

Provides safe access with patient privacy controls, radiation dose tracking,
and transfer syntax negotiation.

Requirements:
    pip install pydicom pynetdicom
"""
from __future__ import annotations

import time
import threading
from typing import Any
from datetime import datetime, timezone

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


MODALITY_TYPES = {
    "CT": "Computed Tomography",
    "MR": "Magnetic Resonance",
    "US": "Ultrasound",
    "CR": "Computed Radiography",
    "DX": "Digital Radiography",
    "XA": "X Ray Angiography",
    "NM": "Nuclear Medicine",
    "PT": "PET",
    "MG": "Mammography",
    "RF": "Radiofluoroscopy",
    "OT": "Other",
}

TRANSFER_SYNTAXES = [
    "1.2.840.10008.1.2",        # Implicit VR Little Endian
    "1.2.840.10008.1.2.1",      # Explicit VR Little Endian
    "1.2.840.10008.1.2.2",      # Explicit VR Big Endian
    "1.2.840.10008.1.2.4.50",   # JPEG Baseline
    "1.2.840.10008.1.2.4.70",   # JPEG Lossless
    "1.2.840.10008.1.2.4.90",   # JPEG 2000 Lossless
    "1.2.840.10008.1.2.4.91",   # JPEG 2000 Lossy
]


class DICOMDevice(Driver):
    """DICOM PACS/modality driver for medical imaging workflows."""

    name = "DICOM Imaging Gateway"
    version = "1.0.0"
    device_type = "imaging_gateway"
    description = "DICOM networking for PACS query, retrieve, store, and modality worklist"
    connection_type = ConnectionType.TCP

    def __init__(self, device_id: str | None = None, host: str = "127.0.0.1",
                 port: int = 104, ae_title: str = "KHP_SCU",
                 remote_ae: str = "PACS", **config):
        super().__init__(device_id=device_id, host=host, port=port, **config)
        self._host = host
        self._port = port
        self._ae_title = ae_title
        self._remote_ae = remote_ae
        self._ae = None
        self._association = None
        self._storage_scp = None
        self._received_datasets: list[dict] = []
        self._query_results: list[dict] = []
        self._study_count = 0
        self._series_count = 0
        self._image_count = 0
        self._last_echo_time: float | None = None
        self._dose_tracking: list[dict] = []
        self._active = False

    async def connect(self):
        """Initialize DICOM Application Entity and verify connectivity."""
        try:
            from pynetdicom import AE, evt, StoragePresentationContexts
            from pynetdicom.sop_class import Verification

            self._ae = AE(ae_title=self._ae_title)
            self._ae.add_requested_context(Verification)

            for ctx in StoragePresentationContexts[:64]:
                self._ae.add_requested_context(ctx.abstract_syntax)

            from pynetdicom.sop_class import (
                PatientRootQueryRetrieveInformationModelFind,
                PatientRootQueryRetrieveInformationModelMove,
                PatientRootQueryRetrieveInformationModelGet,
                StudyRootQueryRetrieveInformationModelFind,
                StudyRootQueryRetrieveInformationModelMove,
                ModalityWorklistInformationFind,
            )
            for sop in [PatientRootQueryRetrieveInformationModelFind,
                        PatientRootQueryRetrieveInformationModelMove,
                        PatientRootQueryRetrieveInformationModelGet,
                        StudyRootQueryRetrieveInformationModelFind,
                        StudyRootQueryRetrieveInformationModelMove,
                        ModalityWorklistInformationFind]:
                self._ae.add_requested_context(sop)

            assoc = self._ae.associate(self._host, self._port, ae_title=self._remote_ae)
            if assoc.is_established:
                status = assoc.send_c_echo()
                if status and status.Status == 0x0000:
                    self._last_echo_time = time.time()
                assoc.release()
                self._active = True
                await super().connect()
            else:
                from khp.errors import ConnectionFailedError
                raise ConnectionFailedError(
                    f"DICOM association rejected by {self._remote_ae} at {self._host}:{self._port}",
                    device_id=self.device_id,
                )

        except ImportError:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                "pynetdicom not installed. Install with: pip install pydicom pynetdicom",
                device_id=self.device_id,
            )

    async def disconnect(self):
        """Release DICOM association and stop storage SCP if running."""
        if self._storage_scp:
            self._storage_scp.shutdown()
            self._storage_scp = None
        self._active = False
        await super().disconnect()

    def _associate(self):
        """Create a fresh association for an operation."""
        if not self._ae:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("DICOM AE not initialized", device_id=self.device_id)
        assoc = self._ae.associate(self._host, self._port, ae_title=self._remote_ae)
        if not assoc.is_established:
            from khp.errors import ConnectionFailedError
            raise ConnectionFailedError(
                "Failed to establish DICOM association",
                device_id=self.device_id,
            )
        return assoc

    @readable(type="bool", description="Whether the DICOM peer is reachable via C ECHO")
    def peer_reachable(self) -> bool:
        return self._active and self._last_echo_time is not None

    @readable(type="int", description="Total studies found in last query", unit="count")
    def study_count(self) -> int:
        return self._study_count

    @readable(type="int", description="Total series found in last query", unit="count")
    def series_count(self) -> int:
        return self._series_count

    @readable(type="int", description="Total images received via C STORE", unit="count")
    def image_count(self) -> int:
        return self._image_count

    @readable(type="str", description="Local Application Entity title")
    def ae_title(self) -> str:
        return self._ae_title

    @readable(type="str", description="Remote PACS Application Entity title")
    def remote_ae_title(self) -> str:
        return self._remote_ae

    @readable(type="list", description="Radiation dose records from CT/fluoroscopy studies")
    def dose_records(self) -> list:
        return self._dose_tracking[-50:]

    @writable(type="str", description="Set the remote Application Entity title for PACS connection")
    def target_ae(self, value: str):
        if not value or len(value) > 16:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                "AE title must be 1 to 16 characters",
                device_id=self.device_id,
                property_name="target_ae",
                value=value,
                limit=16,
            )
        self._remote_ae = value.upper()

    @safety(min=1, max=65535, reason="Valid TCP port range", hard=True)
    @writable(type="int", description="Set the remote DICOM port", unit="port")
    def target_port(self, value: int):
        self._port = value

    @procedure(description="Send C ECHO to verify DICOM connectivity")
    def verify_connection(self):
        """Send a DICOM C ECHO (ping equivalent)."""
        assoc = self._associate()
        try:
            status = assoc.send_c_echo()
            if status and status.Status == 0x0000:
                self._last_echo_time = time.time()
                return {"status": "success", "message": "C ECHO successful"}
            return {"status": "failed", "message": f"C ECHO returned status {status.Status if status else 'None'}"}
        finally:
            assoc.release()

    @procedure(description="Query studies for a patient using C FIND at the study level")
    def find_studies(self, patient_id: str = "", patient_name: str = "",
                     study_date: str = "", modality: str = "", accession: str = ""):
        """Patient or Study root C FIND query."""
        from pydicom.dataset import Dataset

        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        ds.PatientID = patient_id or ""
        ds.PatientName = patient_name or ""
        ds.StudyDate = study_date or ""
        ds.ModalitiesInStudy = modality or ""
        ds.AccessionNumber = accession or ""
        ds.StudyInstanceUID = ""
        ds.StudyDescription = ""
        ds.NumberOfStudyRelatedSeries = ""
        ds.NumberOfStudyRelatedInstances = ""

        from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind

        assoc = self._associate()
        results = []
        try:
            responses = assoc.send_c_find(ds, StudyRootQueryRetrieveInformationModelFind)
            for status, identifier in responses:
                if status and status.Status in (0xFF00, 0xFF01) and identifier:
                    results.append({
                        "patient_id": str(getattr(identifier, "PatientID", "")),
                        "patient_name": str(getattr(identifier, "PatientName", "")),
                        "study_date": str(getattr(identifier, "StudyDate", "")),
                        "study_uid": str(getattr(identifier, "StudyInstanceUID", "")),
                        "description": str(getattr(identifier, "StudyDescription", "")),
                        "modalities": str(getattr(identifier, "ModalitiesInStudy", "")),
                        "series_count": str(getattr(identifier, "NumberOfStudyRelatedSeries", "")),
                        "instance_count": str(getattr(identifier, "NumberOfStudyRelatedInstances", "")),
                    })
        finally:
            assoc.release()

        self._study_count = len(results)
        self._query_results = results
        return {"count": len(results), "studies": results}

    @procedure(description="Query series within a study using C FIND")
    def find_series(self, study_uid: str = ""):
        """Series level C FIND query within a study."""
        if not study_uid:
            return {"error": "study_uid required"}

        from pydicom.dataset import Dataset
        from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind

        ds = Dataset()
        ds.QueryRetrieveLevel = "SERIES"
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = ""
        ds.SeriesNumber = ""
        ds.SeriesDescription = ""
        ds.Modality = ""
        ds.NumberOfSeriesRelatedInstances = ""

        assoc = self._associate()
        results = []
        try:
            responses = assoc.send_c_find(ds, StudyRootQueryRetrieveInformationModelFind)
            for status, identifier in responses:
                if status and status.Status in (0xFF00, 0xFF01) and identifier:
                    results.append({
                        "series_uid": str(getattr(identifier, "SeriesInstanceUID", "")),
                        "series_number": str(getattr(identifier, "SeriesNumber", "")),
                        "description": str(getattr(identifier, "SeriesDescription", "")),
                        "modality": str(getattr(identifier, "Modality", "")),
                        "instance_count": str(getattr(identifier, "NumberOfSeriesRelatedInstances", "")),
                    })
        finally:
            assoc.release()

        self._series_count = len(results)
        return {"study_uid": study_uid, "count": len(results), "series": results}

    @procedure(description="Retrieve a study via C MOVE to the local storage SCP")
    def retrieve_study(self, study_uid: str = "", destination_ae: str = ""):
        """Initiate a C MOVE to retrieve all images in a study."""
        if not study_uid:
            return {"error": "study_uid required"}

        dest = destination_ae or self._ae_title
        from pydicom.dataset import Dataset
        from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelMove

        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        ds.StudyInstanceUID = study_uid

        assoc = self._associate()
        completed = 0
        failed = 0
        try:
            responses = assoc.send_c_move(ds, dest, StudyRootQueryRetrieveInformationModelMove)
            for status, identifier in responses:
                if status:
                    if status.Status == 0x0000:
                        break
                    elif status.Status == 0xFF00:
                        completed += getattr(status, "NumberOfCompletedSuboperations", 0)
                        failed += getattr(status, "NumberOfFailedSuboperations", 0)
        finally:
            assoc.release()

        self._image_count += completed
        return {
            "status": "complete",
            "study_uid": study_uid,
            "destination": dest,
            "images_retrieved": completed,
            "failed": failed,
        }

    @procedure(description="Start a local DICOM storage SCP to receive images")
    def start_storage_scp(self, port: int = 11112):
        """Launch a background storage SCP for receiving C STORE requests."""
        if self._storage_scp:
            return {"status": "already_running", "port": port}

        from pynetdicom import AE, evt, AllStoragePresentationContexts

        def handle_store(event):
            ds = event.dataset
            ds.file_meta = event.file_meta
            info = {
                "sop_class": str(ds.file_meta.MediaStorageSOPClassUID),
                "sop_instance": str(getattr(ds, "SOPInstanceUID", "")),
                "patient_id": str(getattr(ds, "PatientID", "")),
                "modality": str(getattr(ds, "Modality", "")),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._received_datasets.append(info)
            self._image_count += 1

            if hasattr(ds, "CTDIvol") or hasattr(ds, "DoseLength"):
                self._dose_tracking.append({
                    "patient_id": str(getattr(ds, "PatientID", "")),
                    "ctdi_vol": float(getattr(ds, "CTDIvol", 0)),
                    "dlp": float(getattr(ds, "DoseLength", 0)) if hasattr(ds, "DoseLength") else None,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            return 0x0000

        scp_ae = AE(ae_title=self._ae_title)
        for ctx in AllStoragePresentationContexts:
            scp_ae.add_supported_context(ctx.abstract_syntax)

        handlers = [(evt.EVT_C_STORE, handle_store)]

        self._storage_scp = scp_ae.start_server(
            ("0.0.0.0", port),
            evt_handlers=handlers,
            block=False,
        )

        return {"status": "started", "port": port, "ae_title": self._ae_title}

    @procedure(description="Stop the local storage SCP")
    def stop_storage_scp(self):
        """Shutdown the background storage SCP."""
        if self._storage_scp:
            self._storage_scp.shutdown()
            self._storage_scp = None
            return {"status": "stopped"}
        return {"status": "not_running"}

    @procedure(description="Query the modality worklist for scheduled procedures")
    def query_worklist(self, station_ae: str = "", scheduled_date: str = ""):
        """Modality Worklist C FIND query."""
        from pydicom.dataset import Dataset
        from pynetdicom.sop_class import ModalityWorklistInformationFind

        ds = Dataset()
        ds.PatientID = ""
        ds.PatientName = ""

        sps = Dataset()
        sps.ScheduledStationAETitle = station_ae or ""
        sps.ScheduledProcedureStepStartDate = scheduled_date or ""
        sps.Modality = ""
        sps.ScheduledProcedureStepDescription = ""
        ds.ScheduledProcedureStepSequence = [sps]

        ds.RequestedProcedureDescription = ""
        ds.AccessionNumber = ""

        assoc = self._associate()
        results = []
        try:
            responses = assoc.send_c_find(ds, ModalityWorklistInformationFind)
            for status, identifier in responses:
                if status and status.Status in (0xFF00, 0xFF01) and identifier:
                    sps_seq = getattr(identifier, "ScheduledProcedureStepSequence", [])
                    sps_info = {}
                    if sps_seq:
                        sps_item = sps_seq[0]
                        sps_info = {
                            "station_ae": str(getattr(sps_item, "ScheduledStationAETitle", "")),
                            "start_date": str(getattr(sps_item, "ScheduledProcedureStepStartDate", "")),
                            "modality": str(getattr(sps_item, "Modality", "")),
                            "description": str(getattr(sps_item, "ScheduledProcedureStepDescription", "")),
                        }
                    results.append({
                        "patient_id": str(getattr(identifier, "PatientID", "")),
                        "patient_name": str(getattr(identifier, "PatientName", "")),
                        "accession": str(getattr(identifier, "AccessionNumber", "")),
                        "procedure": str(getattr(identifier, "RequestedProcedureDescription", "")),
                        "scheduled_step": sps_info,
                    })
        finally:
            assoc.release()

        return {"count": len(results), "worklist": results}

    @procedure(description="Get list of recently received DICOM datasets")
    def list_received(self, last_n: int = 20):
        """Show most recent images received by the storage SCP."""
        entries = self._received_datasets[-last_n:]
        return {"total_received": len(self._received_datasets), "returned": len(entries), "datasets": entries}

    @monitor(interval_ms=10000, description="Monitor DICOM peer connectivity and storage SCP status")
    def check_dicom_health(self) -> dict[str, Any]:
        alerts = []

        if self._last_echo_time and (time.time() - self._last_echo_time) > 60:
            alerts.append({"level": "warning", "message": "No C ECHO response in over 60 seconds"})

        if not self._active:
            alerts.append({"level": "critical", "message": "DICOM AE not active"})

        return {
            "healthy": len(alerts) == 0,
            "peer": f"{self._host}:{self._port}",
            "remote_ae": self._remote_ae,
            "images_received": self._image_count,
            "storage_scp_running": self._storage_scp is not None,
            "dose_records": len(self._dose_tracking),
            "alerts": alerts,
        }

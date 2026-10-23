############
#
# Copyright (c) 2024-2026 Maxim Yudayev and KU Leuven eMedia Lab
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# Created 2024-2025 for the KU Leuven AidWear, AidFOG, and RevalExo projects
# by Maxim Yudayev [https://yudayev.com].
#
# ############

import queue
import time
from typing import Optional

from hermes.utils.types import LoggingSpec
import numpy as np
from pypylon import pylon
from collections import OrderedDict

from hermes.utils.time_utils import get_time
from hermes.utils.zmq_utils import PORT_BACKEND, PORT_KILL, PORT_SYNC_HOST
from hermes.base.nodes.producer import Producer

from hermes.basler.data_container import CameraDataContainer
from hermes.basler.handler import ImageEventHandler


class CameraProducer(Producer):
    """A class for streaming videos from Basler PoE cameras."""

    def __init__(
        self,
        node_id: str,
        host_ip: str,
        logging_spec: LoggingSpec,
        camera_mapping: dict[str, str],
        fps: float,
        resolution: tuple[int],
        buf_len: Optional[int] = 300,
        pylon_max_buffer_size: Optional[int] = 30,
        port_pub: Optional[str] = PORT_BACKEND,
        port_sync: Optional[str] = PORT_SYNC_HOST,
        port_killsig: Optional[str] = PORT_KILL,
        transmit_delay_sample_period_s: Optional[float] = float("nan"),
        timesteps_before_solidified: Optional[int] = 0,
        **_,
    ):
        camera_names, camera_ids = tuple(zip(*(camera_mapping.items())))
        self._camera_mapping: OrderedDict[str, str] = OrderedDict(
            zip(camera_ids, camera_names)
        )
        self._pylon_max_buffer_size = pylon_max_buffer_size
        self._fps = fps
        self._get_frame_fn = self._get_frame
        self._stop_time_s = float("nan")

        data_out_spec = {
            "camera_mapping": camera_mapping,
            "fps": fps,
            "resolution": resolution,
            "buf_len": buf_len,
            "timesteps_before_solidified": timesteps_before_solidified,
        }

        super().__init__(
            node_id=node_id,
            host_ip=host_ip,
            data_out_spec=data_out_spec,
            logging_spec=logging_spec,
            sampling_rate_hz=fps,
            port_pub=port_pub,
            port_sync=port_sync,
            port_killsig=port_killsig,
            transmit_delay_sample_period_s=transmit_delay_sample_period_s,
        )

    @classmethod
    def create_data_container(cls, data_spec: dict) -> CameraDataContainer:
        return CameraDataContainer(**data_spec)

    def _ping_device(self) -> None:
        return None

    def _connect(self) -> bool:
        tlf: pylon.TlFactory = pylon.TlFactory.GetInstance()
        self._tl: pylon.TransportLayer = tlf.CreateTl("BaslerGigE")

        devices: list[pylon.DeviceInfo] = [
            d
            for d in self._tl.EnumerateDevices()
            if d.GetSerialNumber() in self._camera_mapping.keys()
        ]

        self._cam_array: pylon.InstantCameraArray = pylon.InstantCameraArray(
            len(devices)
        )
        for idx, cam in enumerate(self._cam_array):  # type: ignore
            cam.Attach(self._tl.CreateDevice(devices[idx]))

        self._cam_array.Open()

        for idx, cam in enumerate(self._cam_array):  # type: ignore
            cam.MaxNumBuffer = self._pylon_max_buffer_size
            cam.SetCameraContext(idx)
            cam.PtpEnable.SetValue(True)
            cam.PtpDataSetLatch.Execute()

        for idx, cam in enumerate(self._cam_array):  # type: ignore
            while cam.PtpServoStatus.GetValue() != "Locked":
                cam.PtpDataSetLatch.Execute()
                time.sleep(2)

        self._image_handler = ImageEventHandler(cam_array=self._cam_array)

        self._cam_array.StartGrabbing(
            pylon.GrabStrategy_LatestImages, pylon.GrabLoop_ProvidedByInstantCamera
        )
        return True

    def _process_data(self) -> None:
        self._get_frame_fn()

    def _get_frame(self) -> None:
        # Wait up to 50ms for a frame; if one arrives, process it and drain any backlog
        if buf := self._image_handler.get_frame(timeout=0.05):
            self._process_frame(*buf)
            while True:
                try:
                    buf = self._image_handler._queue.get_nowait()
                    self._process_frame(*buf)
                except queue.Empty:
                    break

    def _get_frame_stopped(self) -> None:
        # Drain all remaining queued frames
        while True:
            try:
                buf = self._image_handler._queue.get_nowait()
                self._process_frame(*buf)
            except queue.Empty:
                break

        if not self._is_continue_capture:
            self._send_end_packet()

    def _keep_samples(self) -> None:
        self._image_handler.keep_data()

    def _process_frame(
        self,
        camera_id: str,
        frame_buffer: np.ndarray,
        is_keyframe: bool,
        frame_index: np.uint64,
        timestamp: np.uint64,
        sequence_id: np.uint64,
        toa_s: float,
    ) -> None:
        process_time_s = get_time()
        data = {
            "frame_timestamp": np.array([[timestamp]], dtype=np.uint64),
            "frame_index": np.array([[frame_index]], dtype=np.uint64),
            "frame_sequence_id": np.array([[sequence_id]], dtype=np.uint64),
            "frame": frame_buffer,
            "toa_s": np.array([[toa_s]], dtype=np.float64),
        }
        self._publish(process_time_s=process_time_s, new_data={camera_id: data})

    def _stop_new_data(self) -> None:
        self._cam_array.StopGrabbing()
        self._stop_time_s = get_time()
        self._get_frame_fn = self._get_frame_stopped

    def _cleanup(self) -> None:
        cam: pylon.InstantCamera
        for cam in self._cam_array:
            cam.DeregisterImageEventHandler(self._image_handler)
        self._cam_array.Close()
        super()._cleanup()

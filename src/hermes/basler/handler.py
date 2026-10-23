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

from collections import OrderedDict
import queue
from pypylon import pylon
import numpy as np

from hermes.utils.time_utils import get_time


class ImageEventHandler(pylon.ImageEventHandler):
    """Basler background callback handler.

    Grabbed images from all devices are pushed into a thread-safe queue
    for the main CameraProducer thread to process. Cameras and host work on
    different timebases.
    """

    def __init__(self, cam_array: pylon.InstantCameraArray):
        super().__init__()
        self._is_keep_data = False
        self._cam_array = cam_array
        cam: pylon.InstantCamera
        # Register with the pylon loop, specify strategy for frame grabbing.
        for cam in cam_array:
            cam.RegisterImageEventHandler(
                self, pylon.RegistrationMode_ReplaceAll, pylon.Cleanup_None
            )
        self._start_sequence_id: OrderedDict[str, np.uint64] = OrderedDict(
            [(cam.GetDeviceInfo().GetSerialNumber(), None) for cam in cam_array]
        )
        self._queue: queue.Queue[
            tuple[str, np.ndarray, bool, np.uint64, np.uint64, np.uint64, float]
        ] = queue.Queue()

    def OnImageGrabbed(self, camera: pylon.InstantCamera, res: pylon.GrabResult):  # type: ignore
        # Gets called on every image.
        #   Runs in a pylon thread context, always wrap in the `try .. except`
        #   to capture errors inside the grabbing as this can't be properly
        #   reported from the background thread to the foreground python code.
        try:
            if res.GrabSucceeded():
                if not self._is_keep_data:
                    res.Release()
                else:
                    toa_s: float = get_time()
                    camera_id: str = str(camera.GetDeviceInfo().GetSerialNumber())
                    timestamp: np.uint64 = np.uint64(res.GetTimeStamp())
                    sequence_id: np.uint64 = np.uint64(res.GetImageNumber())
                    # Presentation time in the units of the timebase of the stream, w.r.t. the start of the video recording.
                    if self._start_sequence_id[camera_id] is None:
                        self._start_sequence_id[camera_id] = sequence_id
                    frame_index = sequence_id - self._start_sequence_id[camera_id]

                    is_keyframe: bool = res.GetNumberOfSkippedImages() > 0

                    # Copy to owned Python array and immediately release Pylon DMA buffer
                    frame = res.Array.copy()[None]
                    res.Release()

                    # 0 copies: pointer transfer to queue
                    self._queue.put(
                        (
                            camera_id,
                            frame,
                            is_keyframe,
                            frame_index,
                            timestamp,
                            sequence_id,
                            toa_s,
                        )
                    )
            else:
                res.Release()
        except Exception as e:
            print(f"Error in OnImageGrabbed: {e}", flush=True)

    def get_frame(
        self, timeout: float = 0.05
    ) -> tuple[str, np.ndarray, bool, np.uint64, np.uint64, np.uint64, float] | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def keep_data(self):
        self._is_keep_data = True

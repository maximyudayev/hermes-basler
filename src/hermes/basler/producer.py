############
#
# Copyright (c) 2024 Maxim Yudayev and KU Leuven eMedia Lab
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

import time

import numpy as np
from pypylon import pylon
from collections import OrderedDict

from hermes.utils.time_utils import get_time
from hermes.utils.zmq_utils import PORT_BACKEND, PORT_KILL, PORT_SYNC_HOST
from hermes.base.nodes.producer import Producer

from hermes.basler.stream import CameraStream
from hermes.basler.handler import ImageEventHandler


class CameraProducer(Producer):
  """A class for streaming videos from Basler PoE cameras.
  """
  @classmethod
  def _log_source_tag(cls) -> str:
    return 'cameras'


  def __init__(self,
               host_ip: str,
               logging_spec: dict,
               camera_mapping: dict[str, str],
               fps: float,
               resolution: tuple[int],
               pylon_max_buffer_size: int = 10,
               port_pub: str = PORT_BACKEND,
               port_sync: str = PORT_SYNC_HOST,
               port_killsig: str = PORT_KILL,
               transmit_delay_sample_period_s: float = float('nan'),
               timesteps_before_solidified: int = 0,
               **_):
    """Constructor of the Basler camera Node.

    Args:
        host_ip (str): IP address of the local master Broker.
        logging_spec (dict): Mapping of Storage object parameters to user-defined configuration values.
        camera_mapping (dict[str, str]): Mapping camera names to device indices.
        fps (float): Frame rate of the video in frames-per-second.
        resolution (tuple[int]): Captured image frame size.
        pylon_max_buffer_size (int, optional): Maximum number of frames to buffer by the kernel driver of the Basler backend. Defaults to 10.
        port_pub (str, optional): Local port to publish to for local master Broker to relay. Defaults to PORT_BACKEND.
        port_sync (str, optional): Local port to listen to for local master Broker's startup coordination. Defaults to PORT_SYNC_HOST.
        port_killsig (str, optional): Local port to listen to for local master Broker's termination signal. Defaults to PORT_KILL.
        transmit_delay_sample_period_s (float, optional): Duration of the period over which to estimate propagation delay of measurements from the corresponding device. Defaults to float('nan').
        timesteps_before_solidified (int, optional): How many most recent samples to keep in memory before flushing. Defaults to 0.
    """
    camera_names, camera_ids = tuple(zip(*(camera_mapping.items())))
    self._camera_mapping: OrderedDict[str, str] = OrderedDict(zip(camera_ids, camera_names))
    self._pylon_max_buffer_size = pylon_max_buffer_size
    self._fps = fps
    self._get_frame_fn = self._get_frame
    self._stop_time_s = float('nan')

    stream_out_spec = {
      "camera_mapping": camera_mapping,
      "fps": fps,
      "resolution": resolution,
      "timesteps_before_solidified": timesteps_before_solidified
    }

    super().__init__(host_ip=host_ip,
                     stream_out_spec=stream_out_spec,
                     logging_spec=logging_spec,
                     sampling_rate_hz=fps,
                     port_pub=port_pub,
                     port_sync=port_sync,
                     port_killsig=port_killsig,
                     transmit_delay_sample_period_s=transmit_delay_sample_period_s)


  @classmethod
  def create_stream(cls, stream_spec: dict) -> CameraStream:
    return CameraStream(**stream_spec)


  def _ping_device(self) -> None:
    return None


  def _connect(self) -> bool:
    tlf: pylon.TlFactory = pylon.TlFactory.GetInstance()

    # Get Transport Layer for just the GigE Basler cameras.
    self._tl: pylon.TransportLayer = tlf.CreateTl('BaslerGigE')

    # Filter discovered cameras by user-defined serial numbers.
    devices: list[pylon.DeviceInfo] = [d for d in self._tl.EnumerateDevices() if d.GetSerialNumber() in self._camera_mapping.keys()]

    # Instantiate cameras.
    cam: pylon.InstantCamera
    self._cam_array: pylon.InstantCameraArray = pylon.InstantCameraArray(len(devices))
    for idx, cam in enumerate(self._cam_array): # type: ignore
      cam.Attach(self._tl.CreateDevice(devices[idx]))

    # Connect to the cameras.
    self._cam_array.Open()

    # Configure the cameras according to the user settings.
    for idx, cam in enumerate(self._cam_array): # type: ignore
      # For consistency load persistent settings stored in the camera.
      # NOTE: avoid overwriting this user set in Pylon viewer.
      # cam.UserSetSelector = "UserSet1"
      # cam.UserSetLoad.Execute()

      # Preload persistent feature configurations saved to a file (easier configuration of all cameras).
      # if self._camera_config_filepath is not None: 
      #   pylon.FeaturePersistence.Load(self._camera_config_filepath, cam.GetNodeMap())
      # Optionally configure ring buffer size if grabbing is slowed down by color conversion.
      # cam.OutputQueueSize = 2*self._fps # The size of the grab result buffer output queue.
      # cam.MaxNumGrabResults = ? # The maximum number of grab results available at any time during a grab session.
      # cam.MaxNumQueuedBuffer = self._pylon_max_buffer_size # The maximum number of buffers that are queued in the stream grabber input queue.
      # cam.MaxNumBuffer = self._pylon_max_buffer_size # The maximum number of buffers that are allocated and used for grabbingam.MaxNumBuffer = self._pylon_max_buffer_size.
      
      # Assign an ID to each grabbed frame, corresponding to the host device.
      cam.SetCameraContext(idx)
      
      # Enable PTP to sync cameras between each other for Synchronous Free Running at the specified frame rate.
      cam.PtpEnable.SetValue(True)
      cam.PtpDataSetLatch.Execute()

    # Verify that the slave device are sufficiently synchronized.
    for idx, cam in enumerate(self._cam_array): # type: ignore
      while cam.PtpServoStatus.GetValue() != "Locked":
        # Execute clock latch.
        cam.PtpDataSetLatch.Execute()
        time.sleep(2)

    # Instantiate callback handler.
    self._image_handler = ImageEventHandler(cam_array=self._cam_array)

    # Start asynchronously capturing images with a background loop.
    # https://docs.baslerweb.com/pylonapi/cpp/pylon_programmingguide#the-default-grab-strategy-one-by-one.
    self._cam_array.StartGrabbing(pylon.GrabStrategy_LatestImages, pylon.GrabLoop_ProvidedByInstantCamera)
    return True


  def _process_data(self) -> None:
    self._get_frame_fn()


  def _get_frame(self) -> None:
    if buf := self._image_handler.get_frame():
      self._process_frame(*buf)


  def _get_frame_stopped(self) -> None:
    is_timeout = (get_time() - self._stop_time_s) > 5
    if buf := self._image_handler.get_frame():
      self._process_frame(*buf)
    elif is_timeout and not self._is_continue_capture:
      # If triggered to stop and no more available data, send empty 'END' packet and join.
      self._send_end_packet()


  def _keep_samples(self) -> None:
    self._image_handler.keep_data()


  def _process_frame(self,
                     camera_id: str,
                     frame_buffer: bytes,
                     is_keyframe: bool,
                     frame_index: np.uint64,
                     timestamp: np.uint64,
                     sequence_id: np.uint64,
                     toa_s: float) -> None:
    process_time_s = get_time()
    tag: str = "%s.%s.data" % (self._log_source_tag(), self._camera_mapping[camera_id])
    data = {
      'frame_timestamp': timestamp,
      'frame_index': frame_index,
      'frame_sequence_id': sequence_id,
      'frame': (frame_buffer, is_keyframe, frame_index),
      'toa_s': toa_s
    }
    self._publish(tag=tag, process_time_s=process_time_s, data={camera_id: data})


  def _stop_new_data(self) -> None:
    # Stop capturing data.
    self._cam_array.StopGrabbing()
    # Change the callback to use a timeout for checking the queue for new packets.
    self._stop_time_s = get_time()
    self._get_frame_fn = self._get_frame_stopped


  def _cleanup(self) -> None:
    # Remove background loop event listener.
    cam: pylon.InstantCamera
    for cam in self._cam_array: 
      cam.DeregisterImageEventHandler(self._image_handler)
    # Disconnect from the camera.
    self._cam_array.Close()
    super()._cleanup()

"""
image_writer.py

Rick Towler
MACE Group
NOAA Alaska Fisheries Science Center

"""

import numpy as np
from PyQt5 import QtCore
import cv2
import av

class ImageWriter(QtCore.QObject):

    SUPPORTED_VIDEO_EXT = ['.avi', '.mp4', '.mpeg4', '.mkv']

    #  define PyQt Signals
    writeComplete = QtCore.pyqtSignal(str, str)
    writerStopped = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str, str)

    def __init__(self, camera_name, parent=None):

        super(ImageWriter, self).__init__(parent)

        self.camera_name = camera_name
        self.frame_number = 0
        self.is_recording = False
        self.avi_stream = None
        self.filename = ''
        self.save_video = False
        self.video_options = {'encoder':'mpeg4', #'h264'
                              'file_ext':'.avi',
                              'framerate':10,
                              'bitrate':1200000,
                              'scale':100,
                              'pixel_format':'yuv420p',
                              'max_frames_per_file': 1000}
        self.save_images = True
        self.image_options = {'file_ext':'.jpg',
                              'jpeg_quality':90,
                              'scale':100}


    @QtCore.pyqtSlot(str, dict)
    def WriteImage(self, camera_name, image_data):
        '''The WriteImage slot writes the provided image to a file using the
        provided fully qualified file name.
        '''

        if self.save_images:
            #  we're writing image files

            #  check if we should scale the image before writing
            if self.image_options['scale'] < 100 and self.image_options['scale'] > 0:
                scale = self.image_options['scale'] / 100.0
                scaled_image = cv2.resize(image_data['data'], (0,0), fx=scale, fy=scale,
                        interpolation=cv2.INTER_AREA)
            else:
                scaled_image = image_data['data']

            #  set the full file name
            if self.image_options['file_ext'][0] != '.':
                self.image_options['file_ext'] = '.' + self.image_options['file_ext']
            filename = image_data['filename'] + self.image_options['file_ext']

            #  check if this is an hdr image and convert if required
            if image_data['is_hdr']:
                #  it is, check if the output format supports full dynamic range
                if self.image_options['file_ext'] not in ['.hdr', '.pic', '.exr']:
                    #  need to convert to 24 bits for this format

                    tonemapDrago = cv2.createTonemapDrago(1.0, 1.5,0.85)
                    scaled_image = tonemapDrago.process(scaled_image)
                    #scaled_image = scaled_image2 * 255

                    scaled_image = np.clip(scaled_image*255, 0, 255).astype('uint8')

            try:
                #  write the image
                if self.image_options['file_ext'] in ['.jpg', '.jpeg']:
                    #  pass the quality flag for JPEGs
                    cv2.imwrite(filename, scaled_image, [int(cv2.IMWRITE_JPEG_QUALITY),
                            self.image_options['jpeg_quality']])
                else:
                    #  no options for this image type
                    cv2.imwrite(filename, scaled_image)

                self.writeComplete.emit(self.camera_name, self.filename)

            except Exception as ex:
                self.error.emit(self.camera_name, 'write_image Error: %s' % ex)


        #  check if we're writing a video frame or image file
        if self.save_video:

            #  check if we should scale the image before writing
            same_image = False
            if not self.save_images or (self.video_options['scale'] !=
                    self.image_options['scale']):

                if self.video_options['scale'] < 100 and self.video_options['scale'] > 0:
                    scale = self.video_options['scale'] / 100.0
                    scaled_image = cv2.resize(image_data['data'], (0,0), fx=scale, fy=scale,
                            interpolation=cv2.INTER_AREA)
                else:
                    #  no need to scale
                    scaled_image = image_data['data']
            else:
                same_image = True

            #  convert this HDR image if we haven't already
            if ((not same_image and image_data['is_hdr']) or
                    (same_image and self.image_options['file_ext'] in ['.hdr', '.pic', '.exr'])):


                #  TODO: implement tonemap conversion here too
                scaled_image = np.clip(scaled_image*255, 0, 255).astype('uint8')

            #  we're recording a video - check if one is currently open
            if self.is_recording:
                #  check if we've hit our max frame limit
                if (self.frame_number >= self.video_options['max_frames_per_file']):
                    #  yes, stop this file and start a new one
                    self.StopRecording(signal_stop=False)

                    #  start a new file
                    if self.video_options['file_ext'][0] != '.':
                        self.video_options['file_ext'] = '.' + self.video_options['file_ext']
                    filename = image_data['filename'] + self.video_options['file_ext']

                    self.StartRecording(filename, scaled_image.shape[1],
                            scaled_image.shape[0])
            else:
                #  we don't have a file open, start a new file
                if self.video_options['file_ext'][0] != '.':
                    self.video_options['file_ext'] = '.' + self.video_options['file_ext']
                filename = image_data['filename'] + self.video_options['file_ext']

                self.StartRecording(filename, scaled_image.shape[1],
                            scaled_image.shape[0])

            #  add this frame
            try:
                self.frame_number = self.frame_number + 1
                frame = av.VideoFrame.from_ndarray(scaled_image, format='bgr24')
                for packet in self.avi_stream.encode(frame):
                    self.avi_writer.mux(packet)

                self.writeComplete.emit(self.camera_name, self.filename)

            except Exception as ex:
                self.error.emit(self.camera_name, 'write_image Error: %s' % ex)


    @QtCore.pyqtSlot(str, int, int)
    def StartRecording(self, filename, width, height):

        if (self.is_recording):
            #  first close the old file before starting a new one
            self.StopRecording()

        try:
            #  create the video writer and set params
            self.avi_writer = av.open(filename, 'w')
            self.avi_stream = self.avi_writer.add_stream(self.video_options['encoder'],
                    self.video_options['framerate'])

            #self.avi_stream.crf=22
#            self.avi_stream.bit_rate = self.video_options['bitrate']
#            if 'bit_rate_tolerance' in  self.video_options:
#                self.avi_stream.bit_rate_tolerance = self.video_options['bit_rate_tolerance']
            self.avi_stream.pix_fmt = self.video_options['pixel_format']
            self.avi_stream.height = height
            self.avi_stream.width = width

            #  reset the frame counter and set the recording state
            self.frame_number = 0
            self.filename = filename
            self.is_recording = True

        except Exception as ex:
            self.is_recording = False
            self.error.emit(self.camera_name, 'Start Recording Error: %s' % ex)


    @QtCore.pyqtSlot()
    def StopRecording(self, signal_stop=True):
        '''the StopRecording slot will close the video file (if writing a video)
        and emit the writerStopped signal when done. If we're just writing stills
        there's nothing to close so this method just emits the signal.

        The signal_stop keyword is used internally to stop without signalling in
        cases where we need to roll the video file (close the old one and open a
        new one.) In this case we don't want to signal we've stopped.
        '''

        if (self.is_recording):
            try:
                self.avi_writer.close()
                self.avi_stream = None
                self.frame_number = 0
                self.filename = ''
                self.is_recording = False
                if signal_stop:
                    self.writerStopped.emit(self.camera_name)
            except Exception as ex:
                self.error.emit(self.camera_name, 'Stop Recording Error: %s' % ex)
        else:
            #  If we're not recording a video, there is nothing to close
            #  so just emit the signal.
            if signal_stop:
                self.writerStopped.emit(self.camera_name)




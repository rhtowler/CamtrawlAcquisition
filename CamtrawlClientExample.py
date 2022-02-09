#!/usr/bin/env python3

'''
ClientExample is a simple example of using the Camtrawl server client.

CamtrawlAcquisition is the application that runs when the camera system boots.
This application reads a configuration file, discovers and configures the
cameras, creates a nested set of directories to store data, then triggers
the cameras and saves the images to disk. While doing this, it also logs
parameters and data from various sensors to a metadata database.

The CamtrawlAcquisition application also provides a simple request/response
server that provides access to the most recent image and sensor data and
also provides basic system control. For purposes of this example we will
focus on getting images and setting metadata.

This example will use the CamtrawlClient module to connect to the server
and request and display images from the attached cameras.

If you do not have a live system to connect to, you can use ImageServerSim.py
to serve up data from a previously recorded deployment.

See the BOTTOM of the script for options.


The following Python packages are required:

numpy
OpenCV 4.x
PyQt5
protobuf

This software was written and tested using the following software packages:

Python 3.7.2 [MSC v.1916 64 bit (AMD64)]
numpy 1.16.2
OpenCV 4.1.2
PyQt 5.12.1
protobuf 3.7.0

'''

import logging
import datetime
import cv2
from CamtrawlServer import CamtrawlClient
from PyQt5 import QtCore

class CamtrawlClientExample(QtCore.QObject):

    def __init__(self, host, port):

        super(CamtrawlClientExample, self).__init__()

        #  store the server's host and port info
        self.host = str(host)
        self.port = int(port)

        #  create an instance of our CamtrawlClient and connect its signals
        self.client = CamtrawlClient.CamtrawlClient()

        #  The imageReceived signal is emitted by the client when it has
        #  received an image from the server.
        self.client.imageData.connect(self.imageReceived)

        #  The syncSensorData and asyncSensorData signals are emitted when the
        #  client receives sensor or control data from the server. In this
        #  example we don't make a distinction between synced and async sensors
        #  so we connect both of them to the same slot.
        self.client.syncSensorData.connect(self.GetSensorData)
        self.client.asyncSensorData.connect(self.GetSensorData)

        #  the parameterData signal is emitted after a getParameter or
        #  set parameter call and it will contain the current parameter
        #  value that was requested or set.
        self.client.parameterData.connect(self.GetParamData)

        #  The error signal is emitted when the client encounters an
        #  error. The errors will primarily be socket related errors.
        self.client.error.connect(self.clientError)

        #  The connected signal is emitted when the client connects to the server.
        self.client.connected.connect(self.connected)

        #  The disconnected signal is emitted when the client is disconnected
        #  from the server
        self.client.disconnected.connect(self.disconnected)

        #  create a logger
        #  get our logger
        self.logger = logging.getLogger(__name__)
        self.logger.propagate = False
        self.logger.setLevel('DEBUG')
        formatter = logging.Formatter('%(asctime)s : %(levelname)s : %(module)s - %(message)s')
        consoleLogger = logging.StreamHandler(sys.stdout)
        consoleLogger.setFormatter(formatter)
        self.logger.addHandler(consoleLogger)

        #  set a timer to allow the event loop to start before continuing
        timer = QtCore.QTimer(self)
        timer.timeout.connect(self.connectToServer)
        timer.setSingleShot(True)
        timer.start(0)


    @QtCore.pyqtSlot()
    def connectToServer(self):
        '''
        connectToServer is called by a timer after the application is instantiated and
        the event loop started. We, er, connect to the server and then request info
        about the cameras.
        '''

        #  connect to the server - the client will emit the connected signal when it's connected.
        self.client.connectToServer(self.host, self.port)


    @QtCore.pyqtSlot(str, str, dict)
    def imageReceived(self, camera, label, imageData):
        '''
        the imageReceived slot is called when the client receives an image from the server.

        It is important to remember that this slot will be called once for each camera
        that has a pending image request on the server. This means that if you request
        a simultaneous pair of images, you must track the received state of each camera
        so you know when you have both images to process.

            camera          - a string containing the camera name the image belongs to.
            label           - a string containing the camera label
            imageData       - a dict in the following form containing the image data and
                              metadata with the following keys:

                                imageData['data'] - image data as OpenCV numpy array
                                imageData['ok'] - True if image was received, False if there was a problem
                                imageData['exposure'] - camera exposure
                                imageData['gain'] - camera gain
                                imageData['height'] - image height
                                imageData['width'] - image width
                                imageData['timestamp'] - trigger timestamp
                                imageData['filename'] - image filename (if any)
                                imageData['image_number'] - global image number

                            imageData['data'] is a numpy array (height, width, depth)
                            and will typically be of dtype 'uint8' (theoretical support
                            for 'uint16' data exists but is not tested.) The depth will
                            be 1 for mono images and 3 for color images. The pixel order
                            is BGR and suitable for use with OpenCV but may need to be
                            converted before using with other image processing libraries.
        '''


        #  In this example we're simply going to display images as they are received.

        #  put some text on the image
        if (len(imageData['data'].shape) == 2):
            #  image is mono
            textColor = (200)
        else:
            textColor = (20,245,20)

        cv2.putText(imageData['data'],'Camera: ' + camera, (10,50),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)
        cv2.putText(imageData['data'],'Label: ' + label, (10,100),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)
        cv2.putText(imageData['data'],'Image number: ' + str(imageData['image_number']), (10,150),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)
        cv2.putText(imageData['data'],'Filename: ' + imageData['filename'], (10,200),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)
        cv2.putText(imageData['data'],'Time: ' + str(imageData['timestamp']), (10,250),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)
        cv2.putText(imageData['data'],'Size: ' + str(imageData['width']) + ' x ' +
                str(imageData['height']), (10,300), cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)
        cv2.putText(imageData['data'],'Exposure: ' + str(imageData['exposure']) + ' us', (10,350),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)
        cv2.putText(imageData['data'],'Gain: ' + str(imageData['gain']), (10,400),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)

        #  and then show it
        cv2.imshow(camera, imageData['data'])

        #  Now request another image from this camera. A new image will be sent
        #  as soon as it is available. Back to back requests for the same camera
        #  will not queue but may cause images to be skipped, especially when
        #  requesting stereo pairs. Regarding stereo pairs, if you want paired
        #  images, you must call getImage and pass a list containing the camera
        #  names of the stereo cameras. When requested separately, the images
        #  may not be synced.
        #
        #  In this example we'll just request data for the camera we just displayed.
        #  The data will not really be synced, but we don't care. You can set the
        #  compressed keyword to True to encode the data as jpeg on the server to
        #  reduce bandwidth requirements at the expense of some CPU cycles. You
        #  can set the scale from 1-100 to scale the image before sending to
        #  further reduce bandwidth requirements. It is also worth scaling if you
        #  plan to scale as part of your image processing.
        self.client.getImage(camera, compressed=False, scale=100, quality=80)


    @QtCore.pyqtSlot(str, str, datetime.datetime, str)
    def GetSensorData(self, sensor_id, header, time, data):
        '''
        The GetSensorData slot is called when the client receives the response
        from a GetData request.
        '''

        self.logger.debug("Sensor Data received: " + str(time) + " : " + sensor_id +
                " : "  + data)


    @QtCore.pyqtSlot(str, str, str, bool, str)
    def GetParamData(self, module, parameter, value, ok, err_string):
        '''
        The GetParamData slot is called when the client receives a response from
        either a getParameter or setParameter request
        '''

        self.logger.debug("Get/SetParameter response received for module: " + module +
            " parameter: " + parameter + " value:" + value + " ok:" + str(ok))


    @QtCore.pyqtSlot()
    def connected(self):

        #  create a dict that will contain our image data
        self.images = {}

        #  create output windows for our cameras
        for cam in self.client.cameras:
            cv2.namedWindow(cam, cv2.WINDOW_NORMAL)

        self.logger.debug("Connected to the server. Requesting images...")

        #  now request images from all of the cameras
        self.client.getImage(self.client.cameras.keys(), compressed=True,
                scale=100, quality=80)


    @QtCore.pyqtSlot()
    def disconnected(self):
        cv2.destroyAllWindows()
        QtCore.QCoreApplication.instance().quit()


    @QtCore.pyqtSlot(int, str)
    def clientError(self, errnum, errorText):
        '''
        the clientError slot is called when the client encounters an error
        '''

        if (errnum == 1):
            #  server closed connection - exit
            self.logger.error("Server closed connection. Exiting...")
        else:
            #  some other socket error
            self.logger.error("Socket Error: %s (%i). Exiting..." % (errorText, errnum))

        cv2.destroyAllWindows()
        QtCore.QCoreApplication.instance().quit()




if __name__ == "__main__":

    import sys

    #  set to the server host IP - if you're running this on the same machine
    #  as CamtrawlAcquisition or ImageServerSim set it to the loopback address.
    #  Otherwise specify the IP of the computer running one of those applications.
    #host = '192.168.0.159'
    host = '127.0.0.1'

    #  set to the server port - the default port for the server is 7889 and it is
    #  set in the CamtrawlAcquisition .ini file.
    port = 7889


    #  create an instance of QCoreApplication
    app = QtCore.QCoreApplication(sys.argv)

    #  create an instance of our example class
    form = CamtrawlClientExample(host, port)

    #  and run
    sys.exit(app.exec_())


#!/usr/bin/env python3

'''
ClientExample is a simple example of using the Camtrawl image server client.

CamtrawlAcquisition is the application that runs when the camera system boots.
This application reads a configuration file, discovers and configures the
cameras, creates a nested set of directories to store data, then triggers
the cameras and saves the images to disk. While doing this, it also logs
parameters and data from various sensors to a metadata database.

The CamtrawlAcquisition applicaiton also provides a simple request/response
server that provides access to the most recent image and sensor data and
also provides basic system control. For purposes of this example we will
focus on getting images and setting metadata.

This example will use the CamtrawlClient module to connect to the server
and request and display images from the attached cameras. It can optionally
inject data into the system's sensor stream showing how to log syncronous
(data logged at the time the cameras are triggered and linked in the database
to that image number) and asyncronous data which is logged immediately
upon receipt and is not linked to an image number.

If you do not have a live system to connect to, you can use ImageServerSim.py
to serve up data from a previously recorded deployment. Note that if you are
using ImageServerSim.py, you will want to make a copy of the metadata database
because when enabled, the example will inject fake data into the database
and you'll want to avoid modifying the original data file.


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

import os
import logging
import datetime
import numpy
import cv2
import CamtrawlClient
from PyQt5 import QtCore

class ClientExample(QtCore.QObject):

    def __init__(self, host, port, calFile, doSetData):

        super(ClientExample, self).__init__()

        #  store the server's host and port info
        self.host = str(host)
        self.port = int(port)

        #  and the path to the calibration file
        if (calFile):
            self.calFile = os.path.normpath(calFile)
        else:
            self.calFile = None

        #  set some default properties
        self.calData = None
        self.doSetData = doSetData
        self.images = {}

        #  create an instance of our CamtrawlClient and connect its signals
        self.imageClient = CamtrawlClient.CamtrawlClient()

        #  The imageReceived signal is emitted by the client when it has
        #  received an image from the server.
        self.imageClient.imageReceived.connect(self.imageReceived)

        #  The dataReceived signal is emitted when the client receives
        #  sensor or control  data from the server.
        self.imageClient.dataReceived.connect(self.dataReceived)

        #  The error signal is emitted when the client encounters an
        #  error. The errors will primarily be socket related errors.
        self.imageClient.error.connect(self.clientError)

        #  The connected signal is emitted when the client connects to the server.
        self.imageClient.connected.connect(self.connected)

        #  The disconnected signal is emitted when the client is disconnected
        #  from teh server
        self.imageClient.disconnected.connect(self.disconnected)

        #  create a logger
        logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)

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

        #  if we're given a cal file path, try to load the cal file
        if (self.calFile):
            try:
                self.calData = self.loadCalibration(self.calFile)
                logging.info("Loaded calibraion file " + self.calFile)

            except:
                logging.error("Unable to load calibraion file " + self.calFile)
                self.calData = None

        #  connect to the server - the client will emit the connected signal when it's connected.
        self.imageClient.connectToServer(self.host, self.port)


    @QtCore.pyqtSlot(str, int, object)
    def imageReceived(self, camera, imageNumber, imageData):
        '''
        the imageReceived slot is called when the client receives an image from the server.

        camera (str)            A string containing the camera name
        imageNumber (int)       An int containing the frame number
        imageData (numpy.array) A numpy array containing the image data. Elements will be uint8
                                and the Z order for color images will be BRG following the OpenCV
                                convention.
        '''

        #  in this example we're not really doing a lot but I demonstrate how to get synced
        #  images and do some basic manipulation and display.

        #  note that we have received an image from this camera - we need to keep track
        #  of this so we know when to request the next image pair
        self.imageClient.cameras[camera]['received'] = True

        #  if we have calibration data, undistort the image
        if (self.calData):
            #  use the camera's label to determine which cal params to use
            camLabel = self.imageClient.cameras[camera]['label'].lower()
            if (camLabel == 'left'):
                imageData = cv2.undistort(imageData,self.calData['cameraMatrixL'],
                        self.calData['distCoeffsL'])
            elif (camLabel == 'right'):
                imageData = cv2.undistort(imageData, self.calData['cameraMatrixR'],
                        self.calData['distCoeffsR'])


        #  put some text on the image - first check if we have a mono or color image
        if (len(imageData.shape) == 2):
            #  image is mono
            textColor = (200)
        else:
            textColor = (20,245,20)
        cv2.putText(imageData,'Image number: ' + str(imageNumber), (10,60),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)
        cv2.putText(imageData,'Label: ' + self.imageClient.cameras[camera]['label'], (10,100),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, textColor, 4)

        #  store this image
        self.images[self.imageClient.cameras[camera]['label']] = imageData


        #  check if we have received images from all cameras
        receivedAll = True
        for cam in self.imageClient.cameras:
            receivedAll &= self.imageClient.cameras[cam]['received']

        #  If we have received images from all of our cameras we will draw them,
        #  reset our received states, request another round of images and optionally
        #  inject some data into the camera's sensor stream which will get stored
        #  in the metadata database for that deployment.
        if (receivedAll):
            for cam in self.imageClient.cameras:
                #  draw this image
                cv2.imshow(cam, self.images[self.imageClient.cameras[cam]['label']])

                #  and reset this camera's received state
                self.imageClient.cameras[cam]['received'] = False

            #  now request another round of images - On Windows, OpenCV scales images to
            #  the window size and they resize nicely so you can set te scale at 100 and
            #  adjust the window to a reasonable size. On Linux, OpenCV doesn't really do an
            #  adjustable window so you'll want to scale the image before display and the
            #  easiest way to do it is to simply request a scaled image. This may or may not
            #  be appropriate for all applications.
            self.imageClient.getImage(self.imageClient.cameras.keys(), compressed=True,
                    scale=100, quality=80)

            #  Here we demonstrate the use of the client's setData method. This method allows us
            #  to inject NMEA 0183 style text data into the camera systems sensor data stream. This
            #  data is then logged in the deployment's metadata database.
            #
            #  NOTE! It is critical that the data string be formatted in the form:
            #
            #  header ID, data
            #
            #  where header ID is a unique identifier for the data contained within the string.
            #
            #  An example using GPS data:
            #
            #    sensorID = "GarminGPS"
            #    data     = ["$GPGGA,134658.00,5106.9792,N,11402.3003,W,2,09,1.0,1048.47,M,-16.27,M,08,AAAA*60",
            #                "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"]
            #
            #  You can see in the above strings that the header IDs are "$GPGGA" and "$GPRMC"
            #
            #  The header IDs do not have to be NMEA 0183 style where there is "$" followed by the 2
            #  character talker ID and the 3 character sentence ID but that is not a bad model to follow.
            #
            #  Here we are simply inserting a string into the async_data table every 10 frames. Note that
            #  we set the asyncSensor keyword to true.
            if (self.doSetData and imageNumber % 10 == 0):
                self.imageClient.setData('AsyncSensor', '$AsyncHeader,' + str(datetime.datetime.now()) +
                    ',This is async data that will be in the async_data table', asyncSensor=True)

            #  Here we are updating syncronous data every 5 frames. Note that we omit the asyncSensor
            #  keyword as by default setData assumes a synced sensor.
            if (self.doSetData and imageNumber % 5 == 0):
                self.imageClient.setData('SyncSensor', '$SyncHeader,' + str(datetime.datetime.now()) +
                    ',This is syned data that will be in the sensor_data table')


    @QtCore.pyqtSlot(str, dict)
    def dataReceived(self, requestType, data):
        '''
        The dataReceived slot is called when the client receives non image data from the
        server such as pitch, yaw, and roll, or depth, system voltage, etc.
        '''

        #  3/7/20 - the getdata and setdata server methods are not complete at this
        #           time so this method is not implemented.
        pass


    @QtCore.pyqtSlot()
    def connected(self):

        #  create a dict that will contain our image data
        self.images = {}

        #  create output windows for our cameras
        for cam in self.imageClient.cameras:
            #  add a dict entry in our images dict for each camera label
            #  if this example we can use the label to distinguish between
            #  the left and right camera.
            self.images[self.imageClient.cameras[cam]['label']] = None

            #  and create a window for this camera
            cv2.namedWindow(cam, cv2.WINDOW_NORMAL)

        #  now request images from all of the cameras
        self.imageClient.getImage(self.imageClient.cameras.keys(), compressed=True,
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
            logging.error("Server closed connection. Exiting...")
        else:
            #  some other socket error
            logging.error("Socket Error: %s (%i). Exiting..." % (errorText, errnum))

        cv2.destroyAllWindows()
        QtCore.QCoreApplication.instance().quit()


    def loadCalibration(self, calfile):
        '''
        loadCalibration reads the numpy .npz file containing the camera calibration data
        created from the CamtrawlCalibrate application. I have inherited this file format
        (I believe it originated from the MATLAB stereo toolkit) and while I would like
        to change it so it doesn't require re-formatting, that is a project for another day.

        The dictionary names should map to OpenCV calibration parameters.

        '''

        #  load the numpy calibration data
        npzfileData = numpy.load(calfile)

        #  reform it
        calData = {}
        calData['cameraMatrixL'] = npzfileData['cameraMatrixL']
        calData['distCoeffsL'] = npzfileData['distCoeffsL']
        calData['kc_left'] = npzfileData['distCoeffsL'][0]
        calData['kc_right'] = npzfileData['distCoeffsR'][0]
        calData['cameraMatrixR'] = npzfileData['cameraMatrixR']
        calData['distCoeffsR'] = npzfileData['distCoeffsR']
        calData['R'] = npzfileData['R']
        calData['T'] = npzfileData['T']
        if 'F' in npzfileData:
            calData['F'] = npzfileData['F']

        calmat = npzfileData['cameraMatrixL']
        calData.update({'fc_left':numpy.array([[calmat[0,0]],[calmat[1,1]]])})
        calData.update({'cc_left':numpy.array([[calmat[0,2]],[calmat[1,2]]])})
        calData.update({'alpha_c_left':numpy.array([[calmat[0,1]]])})

        calmat = npzfileData['cameraMatrixR']
        calData.update({'fc_right':numpy.array([[calmat[0,0]],[calmat[1,1]]])})
        calData.update({'cc_right':numpy.array([[calmat[0,2]],[calmat[1,2]]])})
        calData.update({'alpha_c_right':numpy.array([[calmat[0,1]]])})

        return calData


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

    #  set the full path to the calibration file. Set to None to not load a calibration
    calFile = 'D:/Camtrawl 1 (primary)/camtrawl1_01272020.npz'
    calFile = None

    #  set doSetData to periodically call the client's setData method to set
    #  asynchronous sensor values in the deployment metadata database. This
    #  is provided as an example only - the actual data being written is
    #  meaningless.
    #  NOTE! - If you are replaying data using imageServerSim.py, setting this
    #          to True WILL MODIFY THE DEPLOYMENTS METADATA FILE. Make a copy
    #          of your metadata file before trying this out.
    doSetData = False

    #  create an instance of QCoreApplication
    app = QtCore.QCoreApplication(sys.argv)

    #  create an instance of our example class
    form = ClientExample(host, port, calFile, doSetData)

    #  and run
    sys.exit(app.exec_())


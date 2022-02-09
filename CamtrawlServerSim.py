#!/usr/bin/env python3
'''
ImageServerSim simulates the Camtrawl acquisition server. It reads data
from a Camtrawl deployment folder and serves up images at a specified
rate. This can be used to develop and test applications that interact
with the CamtrawlServer to process data from and/or control a Camtrawl
system. At this time it only implements the GetCameraInfo, GetImage and
GetSensor messages. Calls to the client SetData and SetParameter methods
will only generate text in the simulator's console and will not actually
insert data into the metadata database or set any operational parameters.


The Camtrawl acquisition server uses a simple request/response interface
to provide image and sensor data over a network. The server holds copies
of the most recent images and sensor data acquired and sends them upon
request to clients.

If an image request is received after the last image was sent but before
a new image is available, the server will wait to send the image until
the new image is available. Only the most recent request is queued.
When multiple requests are sent for a single camera, only the newest
request will be responded to. In practice, you should not request a new
image until you have received the previous image and requesting an
image from a camera/cameras more than once may result in skipped images.

See the BOTTOM of the script for options.

The following Python packages are required:

numpy
OpenCV 4.x
PyQt5
protobuf
pywin32 (on windows systems)

This software was written and tested using the following software packages:

Python 3.7.2 [MSC v.1916 64 bit (AMD64)]
numpy 1.16.2
OpenCV 4.1.2
PyQt 5.12.1
protobuf 3.7.0


'''

from PyQt5 import QtCore
import logging
import datetime
import os
import sys
import numpy as np
import cv2
from CamtrawlServer import CamtrawlServer
import CamTrawlMetadata


class CamtrawlServerSim(QtCore.QObject):

    #  define a signal to indicate an external shutdown command was received
    exShutdown = QtCore.pyqtSignal()

    #  parameterChanged is used to respond to Get and SetParam
    #  requests from CamtrawlServer
    parameterChanged = QtCore.pyqtSignal(str, str, str, bool, str)

    stopServer = QtCore.pyqtSignal()
    newImageAvailable = QtCore.pyqtSignal(str, str, dict)


    def __init__(self, deploymentDir, localAddress, localPort,
            repeat=False, startFrame=1, timeScalar=1, startDelay=0,
            parent=None):

        super(CamtrawlServerSim, self).__init__(parent)

        #  set some initial properties
        self.deploymentDir = os.path.normpath(deploymentDir)
        self.dbFile = os.path.normpath(self.deploymentDir + os.sep + 'logs' +
                os.sep + 'CamTrawlMetadata.db3')

        if (startFrame <= 0):
            self.startFrame = 1
        else:
            self.startFrame = startFrame
        self.frameNumber = 0
        self.timeScalar =  timeScalar
        if (startDelay <= 0):
            self.startDelay = 0
        else:
            self.startDelay = startDelay
        self.maxImages = 0
        self.exShutdownRequested = False
        self.repeat = repeat
        self.localAddress = localAddress
        self.localPort = int(localPort)

        #  create an instance of the CamTrawlMetadata class
        self.metadata = CamTrawlMetadata.CamTrawlMetadata()

        #  create a logger
        logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.DEBUG)

        #  connect our external shutdown request signal
        self.exShutdown.connect(self.stopSimulator)

        #  create a timer to update the image data
        self.updateTimer = QtCore.QTimer(self)
        self.updateTimer.timeout.connect(self.updateImages)
        self.updateTimer.setSingleShot(True)

        #  set an event to start the server once the event loop starts
        timer = QtCore.QTimer(self)
        timer.timeout.connect(self.startServer)
        timer.setSingleShot(True)
        timer.start(0)


    @QtCore.pyqtSlot()
    def startServer(self):

        print()
        logging.info('Starting CamtrawlServerSim with deployment ' + self.deploymentDir)
        logging.info('Start Delay: ' + str(self.startDelay) + " seconds")
        logging.info('Replay time scalar: ' + str(self.timeScalar))

        #  open the deployment database
        try:
            logging.info('Opening deployment database ' + self.dbFile)
            self.metadata.open(self.deploymentDir)
            self.metadata.query()
        except:
            logging.critical('Error opening SQLite database file ' + self.dbFile +'. Unable to continue.')
            QtCore.QCoreApplication.instance().quit()
            return

        #  create a numpy array of image intervals in ms
        imageTimes = np.array(list(self.metadata.sensorData['time'].values()),
                dtype='datetime64')
        self.intervals = np.diff(imageTimes)
        self.intervals = np.insert(self.intervals, 0, self.intervals[0])
        #  convert from us to ms
        self.intervals = self.intervals / 1000.

        #  set the starting frame number relative to the first image in the dataset
        self.frameNumber = self.startFrame + self.metadata.startImage - 1

        #  set the max image number
        self.maxImages = self.metadata.endImage

        #  check if the start frame rolled us over
        if self.frameNumber >= self.maxImages:
            self.frameNumber = self.metadata.startImage

        #  report what we found
        for cam in self.metadata.cameras:
            self.metadata.cameras[cam]['nimages'] = len((self.metadata.imageData[cam]))
            logging.info("Found camera " + cam + " labeled '" + self.metadata.cameras[cam]['label'] +
                    "' with " + str(self.metadata.cameras[cam]['nimages']) + " images.")

        #  start an instance of CamtrawlServer and get it all hooked up
        logging.info("Opening Camtrawl server on  " +
                self.localAddress + ":" + str(self.localPort))

        #  create an instance of CamtrawlServer
        self.server = CamtrawlServer(self.localAddress, self.localPort)

        #  connect the server's signals
        self.server.syncSensorData.connect(self.rxSyncSensorData)
        self.server.asyncSensorData.connect(self.rxAsyncSensorData)
        self.server.getParameterRequest.connect(self.rxGetParameterRequest)
        self.server.setParameterRequest.connect(self.rxSetParameterRequest)
        self.server.error.connect(self.serverError)
        self.server.serverClosed.connect(self.finishShutdown)
        self.newImageAvailable.connect(self.server.newImageAvailable)
        self.stopServer.connect(self.server.stopServer)

        #  connect our signals to the server
        self.parameterChanged.connect(self.server.parameterDataAvailable)

        #  create a thread to run CamtrawlServer
        self.serverThread = QtCore.QThread(self)

        #  move the server to it
        self.server.moveToThread(self.serverThread)

        #  connect thread specific signals and slots - this facilitates starting,
        #  stopping, and deletion of the thread.
        self.serverThread.started.connect(self.server.startServer)
        self.server.serverClosed.connect(self.serverThread.quit)
        self.serverThread.finished.connect(self.serverThread.deleteLater)

        #  and finally, start the thread - this will also start the server
        self.serverThread.start()

        #  and start the image update timer
        self.updateTimer.start(self.startDelay)


    @QtCore.pyqtSlot(str)
    def serverError(self, errorStr):
        '''
        slot called when the CamtrawlServer runs into a problem
        '''
        logging.warning("CamtrawlServer error: " + errorStr)


    #  we only report receiving the following messages from the client. Implementing
    #  anything more is beyond the scope of this example

    @QtCore.pyqtSlot(str, str, datetime.datetime, str)
    def rxSyncSensorData(self, id, header, timeObj, data):
        logging.info("Sync'd sensor data received from client: " + id + " ::: " + str(timeObj) + " ::: " + data)

    @QtCore.pyqtSlot(str, str, datetime.datetime, str)
    def rxAsyncSensorData(self, id, header, timeObj, data):
        logging.info("Async sensor data received from client: " + id + " ::: " + str(timeObj) + " ::: " + data)

    @QtCore.pyqtSlot(str, str)
    def rxGetParameterRequest(self, module, parameter):
        logging.info("GetParameterRequest received from client: " + module + " ::: " + parameter)

    @QtCore.pyqtSlot(str, str, str)
    def rxSetParameterRequest(self, module, parameter, value):
        logging.info("SetParameterRequest received from client: " + module + " ::: " + parameter + ":" + value)


    @QtCore.pyqtSlot()
    def updateImages(self):
        '''
        updateImages is called by the image update timer. It loads the next image
        in the dataset for each camera. It also checks if there are any pending
        image requests and services those requests if needed.
        '''

        #  load the next image for each camera
        for cam in self.metadata.cameras:

            #  generate the path for this camera's image
            filepath = self.deploymentDir + os.sep + "images" + os.sep + cam + os.sep

            try:
                #  Get the image name for this camera/frame. Older versions of CamtrawlAcquisition
                #  recorded filenames without extensions. We handle both types here.
                filename, ext = os.path.splitext(self.metadata.imageData[cam][self.frameNumber]['filename'])
                if ext == '' or len(ext) > 4:
                    #  no extension - add it
                    imageFile = (self.metadata.imageData[cam][self.frameNumber]['filename'] + '.' +
                            self.metadata.deploymentData['image_file_type'])
                else:
                    #  filename already has extension
                    imageFile = self.metadata.imageData[cam][self.frameNumber]['filename']
            except:
                #  frame is not available, camera must have dropped the image
                #  during acquisition.
                logging.info("Camera " + cam + " is missing image number " +
                        str(self.frameNumber) + ".")

            try:
                #  read the image data
                imageData = {}
                imageData['ok'] = True
                imageData['image_number'] = self.frameNumber
                imageData['filename'] = imageFile
                imageData['data'] = cv2.imread(filepath + imageFile, cv2.IMREAD_UNCHANGED)

                #  and set the other image properties
                imageData['timestamp'] = self.metadata.imageData[cam][self.frameNumber]['time']
                try:
                    imageData['exposure'] = int(self.metadata.imageData[cam][self.frameNumber]['exposure'])
                except:
                    imageData['exposure'] = -999
                try:
                    imageData['gain'] = int(self.metadata.imageData[cam][self.frameNumber]['gain'])
                except:
                    imageData['gain'] = -999

                self.newImageAvailable.emit(cam, self.metadata.cameras[cam]['label'], imageData)

            except:
                #  there was an issue loading the file
                logging.error("Unable to open image file " + filepath + imageFile)


        #  set up the next timer event
        timerInterval = int(self.intervals[self.frameNumber].astype('float') / self.timeScalar)

        #  increment our frame counter
        self.frameNumber += 1

        #  check if we're at the end of our list of images
        if (self.frameNumber >= self.maxImages):
            if self.repeat:
                #  set the starting frame number relative to the first image in the dataset
                self.frameNumber = self.startFrame + self.metadata.startImage - 1

                #  check if the start frame rolled us over
                if self.frameNumber >= self.maxImages:
                    self.frameNumber = self.metadata.startImage

                logging.info("All images have been served up - Repeat = True - Restarting with image number " +
                        str(self.frameNumber) + ".")
            else:
                #  we're not repeating and we've worked thru all images
                #  so we'll shut down and exit
                logging.info("All images have been served up - Repeat = False - Shutting down image server.")
                self.stopSimulator()
                QtCore.QCoreApplication.instance().quit()

        self.updateTimer.start(timerInterval)


    @QtCore.pyqtSlot()
    def stopSimulator(self):

        #  stop the update timer
        logging.debug("Stopping image update timer...")
        self.updateTimer.stop()

        #  close the metadata database
        self.metadata.close()

        logging.debug("Shutting down the server...")
        self.stopServer.emit()


    @QtCore.pyqtSlot()
    def finishShutdown(self):

        #  if we've been told to shut down from an external signal, exit the application.
        if (self.exShutdownRequested):
            QtCore.QCoreApplication.instance().quit()


    def emitShutdown(self):
        '''
        emitShutdown emits the shutdown signal which will shut down the server.
        We can call this method from outside the event loop context to shut
        the server down without making Qt angry because the actual shutdown
        happenes within the event loop.
        '''
        self.exShutdownRequested = True
        self.exShutdown.emit()


def exitHandler(a,b=None):
    '''
    exitHandler is called when CTRL-c is pressed within the console
    running the server for Windows systems
    '''
    print("CTRL-C detected. Shutting down...")
    server.emitShutdown()

    return True


def signal_handler(*args):
    '''
    signal_handler is called on Linux machines when ctrl-c is pressed when the
    python console has focus or when the terminal window is closed or when
    the Python process gets the SIGTERM signal.
    '''
    print("CTRL-C or SIGTERM/SIGHUP detected. Shutting down...")
    server.emitShutdown()

    return True


if __name__ == "__main__":

    #  path to the deployment folder
    deploymentDir = 'C:/Users/rick.towler/Desktop/D20200309-T012051'

    #  server update rate in images per second
    updateRate = 2

    #  server local address and port - for testing this will almost always be
    #  the loopback interface (127.0.0.1).
    localAddress = '127.0.0.1'

    #  server port - The default port for the Camtrawl server is 7889
    localPort = 7889

    #  delay in seconds before the server starts updating images. Connections
    #  to the server will be accepted when delaying, but images will not be
    #  served up until the delay period ends.
    startDelay = 0

    #  set the time scalar. Setting the scalar to 1 will replay the data in
    #  real time, 2 would be 2x replay, 0.5 would be half real time.
    timeScalar = 1

    #  set repeat to True to loop back to the first image after all images
    #  in a deployment are read
    repeat = True

    #  set start frame to the image number the server should start with.
    #  NOTE: This is relative to the first image available. If the deployment
    #  was trimmed using CamtrawlBrowser, the first image available may not
    #  be image # 1.
    startFrame = 1

    #  create an application instance
    app = QtCore.QCoreApplication(sys.argv)

    #  create the main application window
    server = CamtrawlServerSim(deploymentDir, localAddress, localPort,
            repeat=repeat, startDelay=startDelay, startFrame=startFrame,
            timeScalar=timeScalar)

    #  install a handler to catch ctrl-C on windows
    if sys.platform == "win32":
        import win32api
        win32api.SetConsoleCtrlHandler(exitHandler, True)
    else:
        #  On linux we can use signal
        import signal
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGHUP, signal_handler)

    #  start event processing
    sys.exit(app.exec_())


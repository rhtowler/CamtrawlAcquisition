
"""
CamtrawlAcquisition.py is the image acquisition application for the
Camtrawl image acquisition platform. The application can be used to
collect images and video from Flir machine vision cameras compatible
with the Flir Spinnaker SDK. Experimental support for V4L2 cameras
is available.

If available, the application will connect to the Camtrawl power and
control interface (aka the Camtrawl Controller) to log its sensor
streams and trigger the cameras and strobes. If the controller is not
available, the application will trigger cameras using software triggering.


Rick Towler
MACE Group
NOAA Alaska Fisheries Science Center

"""

from PyQt5 import QtCore
import collections
import logging
import datetime
import os
import yaml
import signal
import PySpin
import SpinCamera
import CamtrawlController
from CamtrawlServer import CamtrawlServer
from metadata_db import metadata_db




class CamtrawlAcquisition(QtCore.QObject):

    # CAMERA_CONFIG_OPTIONS defines the default camera configuration options.
    CAMERA_CONFIG_OPTIONS = {'exposure_us':4000,
                             'gain':18,
                             'label':'Camera',
                             'rotation':'none',
                             'trigger_divider': 1,
                             'save_image_divider': 1,
                             'trigger_source': 'Software',
                             'controller_trigger_port': 1,
                             'hdr_enabled':False,
                             'hdr_save_merged':False,
                             'hdr_signal_merged':False,
                             'hdr_merge_method':'mertens',
                             'hdr_save_format': 'hdr',
                             'hdr_settings':None,
                             'hdr_response_file': None,
                             'hdr_tonemap_saturation': 1.0,
                             'hdr_tonemap_bias': 0.85,
                             'hdr_tonemap_gamma': 2.0,
                             'save_stills': True,
                             'still_image_extension': '.jpg',
                             'jpeg_quality': 90,
                             'image_scale': 100,
                             'save_video': False,
                             'video_preset': 'default',
                             'video_force_framerate': -1,
                             'video_scale': 100}

    #DEFAULT_VIDEO_PROFILE defines the default options for the 'default' video profile.
    DEFAULT_VIDEO_PROFILE = {'encoder': 'mpeg4',
                             'file_ext': '.mp4',
                             'framerate': 10,
                             'bitrate': 1200000,
                             'bit_rate_tolerance': 528000,
                             'scale': 100,
                             'pixel_format': 'yuv420p',
                             'max_frames_per_file': 1000}


    #  define PyQt Signals

    #  define signals used to communicate with the camera objects
    stopAcquiring = QtCore.pyqtSignal(list)
    startAcquiring = QtCore.pyqtSignal((list, str, bool, dict, bool, dict))
    trigger = QtCore.pyqtSignal(list, int, datetime.datetime, bool, bool)

    #  parameterChanged is used to respond to Get and SetParam
    #  requests from CamtrawlServer
    parameterChanged = QtCore.pyqtSignal(str, str, str, bool, str)

    #  stopAppHelper is used to tell the helpers like CamtrawlControl
    #  and CamtrawlServer to stop.
    stopAppHelper = QtCore.pyqtSignal()

    #  specify the application version
    VERSION = '3.0'

    #  specify the maximum number of times the application will attempt to open a
    #  metadata db file when running in combined mode and the original db file
    #  cannot be opened.
    MAX_DB_ALTERNATES = 20


    def __init__(self, config_file=None, profiles_file=None, parent=None):

        super(CamtrawlAcquisition, self).__init__(parent)

        #  Set the configuration file path if provided
        if config_file:
            self.config_file = config_file
        else:
            self.config_file = './CamtrawlAcquisition.yml'
        self.config_file = os.path.normpath(self.config_file)
        if profiles_file:
            self.profiles_file = profiles_file
        else:
            self.profiles_file = './VideoProfiles.yml'
        self.profiles_file = os.path.normpath(self.profiles_file)

        # Define default properties
        self.shutdownOnExit = False
        self.isExiting = False
        self.isAcquiring = False
        self.controller = None
        self.serverThread = None
        self.server = None
        self.controllerStarting = False
        self.system = None
        self.cameras = []
        self.threads = []
        self.hw_triggered_cameras = []
        self.received = {}
        self.use_db = True
        self.sensorData = {}
        self.readyToTrigger = {}
        self.HWTriggerHDR = {}
        self.controller_port = {}

        # The following dicts contain the default configuration data. Normally
        # these values are set in the .yml configuration files. The values here
        # are used if no entry exists in the .yml file.



        # configuration stores the parsed application configuration data. Here we define
        # the default values.  These will be updated with values in the config file.
        self.configuration = {}
        self.configuration['application'] = {}
        self.configuration['controller'] = {}
        self.configuration['server'] = {}
        self.configuration['acquisition'] = {}
        self.configuration['cameras'] = {}
        self.configuration['sensors'] = {}

        self.configuration['application']['output_mode'] = 'separate'
        self.configuration['application']['output_path'] = './data'
        self.configuration['application']['log_level'] = 'DEBUG'
        self.configuration['application']['database_name'] = 'CamtrawlMetadata.db3'
        self.configuration['application']['shut_down_on_exit'] = True
        self.configuration['application']['always_trigger_at_start'] = False

        self.configuration['controller']['use_controller'] = False
        self.configuration['controller']['serial_port'] = 'COM3'
        self.configuration['controller']['baud_rate'] = 57600
        self.configuration['controller']['strobe_pre_fire'] = 150

        self.configuration['server']['start_server'] = True
        self.configuration['server']['server_port'] = 7889
        self.configuration['server']['server_interface'] = '0.0.0.0'

        self.configuration['acquisition']['trigger_rate'] = 5
        self.configuration['acquisition']['trigger_limit'] = -1
        self.configuration['acquisition']['save_stills'] = True
        self.configuration['acquisition']['still_image_extension'] = '.jpg'
        self.configuration['acquisition']['jpeg_quality'] = 90
        self.configuration['acquisition']['image_scale'] = 100
        self.configuration['acquisition']['save_video'] = False
        self.configuration['acquisition']['video_preset'] = 'default'
        self.configuration['acquisition']['video_scale'] = 100

        self.configuration['sensors']['default_type'] = 'synchronous'
        self.configuration['sensors']['synchronous'] = ['$OHPR']
        self.configuration['sensors']['asynchronous'] = ['$CTCS', '$SBCS', '$IMUC', '$CTSV']
        self.configuration['sensors']['synchronous_timeout'] = 5

        #  Create an instance of metadata_db which is a simple interface to the
        #  camtrawl metadata database
        self.db = metadata_db()

        #  create the trigger timer
        self.triggerTimer = QtCore.QTimer(self)
        self.triggerTimer.timeout.connect(self.TriggerCameras)
        self.triggerTimer.setSingleShot(True)
        self.triggerTimer.setTimerType(QtCore.Qt.PreciseTimer)

        #  create the shutdown timer - this is used to delay application
        #  shutdown when no cameras are found. It allows the user to exit
        #  the application and fix the issue when the application is set
        #  to shut the PC down upon exit.
        self.shutdownTimer = QtCore.QTimer(self)
        self.shutdownTimer.timeout.connect(self.AcqisitionTeardown)
        self.shutdownTimer.setSingleShot(True)

        #  continue the setup after QtCore.QCoreApplication.exec_() is called
        #  by using a timer to call AcquisitionSetup. This ensures that the
        #  application event loop is running when AcquisitionSetup is called.
        startTimer = QtCore.QTimer(self)
        startTimer.timeout.connect(self.AcquisitionSetup)
        startTimer.setSingleShot(True)
        startTimer.start(0)


    def AcquisitionSetup(self):
        '''AcquisitionSetup reads the configuration files, creates the log file
        and sets up the application

        '''

        #  get the application start time
        start_time_string = datetime.datetime.now().strftime("D%Y%m%d-T%H%M%S")

        #  read the configuration file - we start with the default values and
        #  recursively update them with values from the config file in the
        #  ReadConfig method.
        self.configuration = self.ReadConfig(self.config_file, self.configuration)

        #  Do the same thing with the video profiles file
        self.video_profiles = self.ReadConfig(self.profiles_file, {})

        #  set the default sensor datagram treatment
        if self.configuration['sensors']['default_type'].lower() == 'synchronous':
            self.default_is_synchronous = True
        else:
            self.default_is_synchronous = False

        #  set up the application paths
        if self.configuration['application']['output_mode'].lower() == 'combined':
            #  This is a combined deployment - we will not create a deployment directory
            self.base_dir = os.path.normpath(self.configuration['application']['output_path'])
        else:
            #  If not 'combined' we log data in separate deployment folders. Deployment folders
            #  are named Dyymmdd-Thhmmss where the date and time are derived from the application
            #  start time.
            self.base_dir = os.path.normpath(self.configuration['application']['output_path'] +
                    os.sep + start_time_string)

        #  create the paths to our logs and images directories
        self.log_dir = os.path.normpath(self.base_dir + os.sep + 'logs')
        self.image_dir = os.path.normpath(self.base_dir + os.sep + 'images')

        #  open the log file
        try:
            logfile_name = self.log_dir + os.sep + start_time_string + '.log'

            #  make sure we have a directory to log into
            if not os.path.exists(self.log_dir):
                os.makedirs(self.log_dir)

            #  create the logger
            self.logger = logging.getLogger(__name__)
            self.logger.propagate = False
            self.logger.setLevel(self.configuration['application']['log_level'])
            fileHandler = logging.FileHandler(logfile_name)
            formatter = logging.Formatter('%(asctime)s : %(levelname)s : %(module)s - %(message)s')
            fileHandler.setFormatter(formatter)
            self.logger.addHandler(fileHandler)
            consoleLogger = logging.StreamHandler(sys.stdout)
            self.logger.addHandler(consoleLogger)

        except:
            #  we failed to open the log file - bail
            print("CRITICAL ERROR: Unable to create log file " + logfile_name)
            print("Application exiting...")
            QtCore.QCoreApplication.instance().quit()
            return

        #  make sure we have a directory to write images into
        try:
            if not os.path.exists(self.image_dir):
                os.makedirs(self.image_dir)
        except:
            #  if we can't create the logging dir we bail
            self.logger.critical("Unable to create image logging directory %s." % self.image_dir)
            self.logger.critical("Application exiting...")
            QtCore.QCoreApplication.instance().quit()
            return



        #  log file is set up and directories created - note it and keep moving
        self.logger.info("Camtrawl Acquisition Starting...")
        self.logger.info("CamtrawlAcquisition version: " + self.VERSION)

        #  open/create the image metadata database file
        self.OpenDatabase()

        #  configure the cameras...
        ok = self.ConfigureCameras()

        if ok:
            #  the cameras are ready to acquire
            self.isAcquiring = True

            #  set up the server
            if self.configuration['server']['start_server']:
                self.StartServer()

            #  set up the controller
            if self.configuration['controller']['use_controller']:
                #  we're using the Camtrawl controller. It will signal the
                #  system state after connecting which we'll use to determine
                #  if we should start triggering or not.
                self.StartController()
            else:
                #  if we're not using the controller, we know we're software
                #  triggering so we just start the timer. We set a long interval
                #  for this first trigger to allow the cameras time to finish
                #  getting ready.
                self.triggerTimer.start(500)
        else:
            #  we were unable to find any cameras
            self.logger.error("Since there are no available cameras we cannot proceed " +
                    "and the application will exit.")

            #  check if we're supposed to shut down. If so, we will delay the shutdown to
            #  allow the user to exit the app before the PC shuts down and correct the problem.
            if self.configuration['application']['shut_down_on_exit']:
                self.logger.error("Shutdown on exit is set, but the application will delay shutdown " +
                        "to allow some time to circumvent the shutdown and correct the issue.")
                self.logger.error("You can exit the application by pressing CTRL-C to " +
                        "circumvent the shutdown and keep the PC running.")

                #  set the shutdownOnExit attribute so we, er shutdown on exit
                self.shutdownOnExit = True

                #  wait 5 minutes before shutting down
                shutdownTimer = QtCore.QTimer(self)
                shutdownTimer.timeout.connect(self.AcqisitionTeardown)
                shutdownTimer.setSingleShot(True)
                shutdownTimer.start(5000 * 60)

            else:
                #  Stop acquisition and close the app
                self.StopAcquisition(exit_app=True, shutdown_on_exit=False)


    def StartController(self):
        '''
        StartController sets up and starts the CamtrawlController interface.
        CamtrawlController is an interface for the Camtrawl power and control
        board which provides power control, sensor integration, and camera and
        strobe triggering for the Camtrawl camera platform.
        '''
        self.logger.info("Connecting to Camtrawl controller on port: " +
                self.configuration['controller']['serial_port'] + " baud: " +
                self.configuration['controller']['baud_rate'])

        #  create an instance of CamtrawlController
        self.controller = CamtrawlController.CamtrawlController(serial_port=
                self.configuration['controller']['serial_port'], baud=
                self.configuration['controller']['baud_rate'])

        #  connect its signals
        self.controller.sensorDataAvailable.connect(self.SensorDataAvailable)
        self.controller.systemState.connect(self.ControllerStateChanged)
        self.controller.error.connect(self.ControllerError)

        #  and start the controller object - we set the controllerStarting
        #  attribute so we know if we receive an error signal from the
        #  controller we know that the controller serial port could not be opened.
        self.controllerStarting = True
        self.controller.startController()


    def StartServer(self):
        '''StartServer will start the CamtrawlServer.
        '''

        self.logger.info("Opening Camtrawl server on  " +
                self.configuration['server']['server_interface'] + ":" +
                str(self.configuration['server']['server_port']))

        #  create an instance of CamtrawlServer
        self.server = CamtrawlServer.CamtrawlServer(
                self.configuration['server']['server_interface'],
                self.configuration['server']['server_port'])

        #  connect the server's signals
        self.server.sensorData.connect(self.SensorDataAvailable)
        #self.server.getParameterRequest.connect(self.ServerGetParamRequest)
        #self.server.setParameterRequest.connect(self.ServerSetParamRequest)
        #self.server.error.connect(self.serverError)

        #  connect our signals to the server
        self.parameterChanged.connect(self.server.parameterDataAvailable)

        #  connect our cameras imageData signals to the server
        for c in self.cameras:
            c.imageData.connect(self.server.newImageAvailable)

        #  create a thread to run CamtrawlServer
        self.serverThread = QtCore.QThread(self)

        #  move the server to it
        self.server.moveToThread(self.serverThread)

        #  connect thread specific signals and slots - this facilitates starting,
        #  stopping, and deletion of the thread.
        self.serverThread.started.connect(self.server.startServer)
        self.server.serverClosed.connect(self.serverThread.quit)
        #self.serverThread.finished.connect(self.appThreadFinished)
        self.serverThread.finished.connect(self.serverThread.deleteLater)

        #  and finally, start the thread - this will also start the server
        self.serverThread.start()


    @QtCore.pyqtSlot(str, str, datetime.datetime, str,)
    def SensorDataAvailable(self, sensor_id, header, rx_time, data):
        '''
        The SensorDataAvailable slot is called when the CamtrawlController
        emits the sensorData signal. This signal is emitted when sensor data i
        received from the controller. It can also be emitted by the CamtrawlServer
        if sensor data is received by the server.

        CamtrawlAcquisition lumps sensor data into 2 groups. Synced sensor data
        is cached when received and then logged to the database when the cameras
        are triggered and the data are linked to the image. Async sensor data is
        logged immediately and is not linked to any image.

        Args:
            sensor_id (TYPE):
                DESCRIPTION
            header (TYPE):
                DESCRIPTION
            rx_time (TYPE):
                DESCRIPTION
            data (TYPE):
                DESCRIPTION

        Returns:
            None


        '''

        #  determine if this data is synced or async
        is_synchronous = self.default_is_synchronous
        if header in self.configuration['sensors']['synchronous']:
            is_synchronous = True
        elif header in self.configuration['sensors']['asynchronous']:
            is_synchronous = False

        if is_synchronous:
            #  this data should be cached to be written to the db when
            #  the cameras are triggered

            #  first check if we have an entry for this sensor
            if sensor_id not in self.sensorData:
                #  nope, add it
                self.sensorData[sensor_id] = {}

            #  add the data
            self.sensorData[id][header] = {'time':rx_time, 'data':data}

        else:
            #  this is async sensor data so we just write it
            if self.use_db:
                self.db.insert_async_data(sensor_id, header, rx_time, data)


    @QtCore.pyqtSlot(int)
    def ControllerStateChanged(self, new_state):
        '''
        the ControllerStateChanged slot is called when the Camtrawl controller emits
        a state change message. The Camtrawl controller operates in one of a number of
        states based on the sensor and logic inputs. When it changes state it will
        emit the systemState signal and this slot will act based on the controller
        state.

        Args:
            new_state (TYPE):
                DESCRIPTION

        Returns:
            None
        '''

        #  When the controller starts, it immediately sends a getState request.
        #  The response indicates that the controller started and is communicating
        #  so we can unset the controllerStarting state.
        if self.controllerStarting:
            self.controllerStarting = False


        if self.controllerCurrentState == new_state:
            #  If the state hasn't changed we just return. This wouldn't
            #  normally happen
            return

        self.logger.info("Camtrawl controller state changed. New state is " +
                str(new_state))

        if ((new_state == self.controller.FORCED_ON) and not
                self.configuration['application']['always_trigger_at_start']):
            #  the system has been forced on and we're not being forced to start
            #  so we *do not* start triggering.

            self.logger.info("System operating in download mode.")

        elif ((new_state == self.controller.FORCED_ON) and
                self.configuration['application']['always_trigger_at_start']):
            #  the system has been forced on and we're configured to always
            #  trigger when starting so we start the trigger timer.

            self.logger.info("System operating in forced trigger mode - starting triggering...")
            self.internalTriggering = True
            #  The first trigger interval is long to ensure the cameras are ready
            self.triggerTimer.start(500)

        elif ((new_state == self.controller.FORCED_ON) and
                self.configuration['application']['always_trigger_at_start']):
            #  the system has been forced on and we're configured to always
            #  trigger when starting so we start the trigger timer.

            self.logger.info("System operating in forced trigger mode - starting triggering...")
            self.internalTriggering = True
            #  The first trigger interval is long to ensure the cameras are ready
            self.triggerTimer.start(500)

        elif new_state == self.controller.AT_DEPTH:
            #  the pressure sensor reports a depth >= the controller turn on depth
            #  We assume we're deployed at depth

            self.logger.info("System operating in deployed mode (@depth) - starting triggering...")
            self.internalTriggering = True
            #  The first trigger interval is long to ensure the cameras are ready
            self.triggerTimer.start(500)

        elif new_state == self.controller.PRESSURE_SW_CLOSED:
            #  the "pressure switch" has closed - we assume we're deployed at depth

            self.logger.info("System operating in deployed mode (p-switch) - starting triggering...")
            self.internalTriggering = True
            #  The first trigger interval is long to ensure the cameras are ready
            self.triggerTimer.start(500)

        elif new_state >= self.controller.FORCE_ON_REMOVED:
            #  The controller is in one of many shutdown states - we'll
            #  branch on the type to report why we're shutting down
            #  then shut down.
            if new_state == self.controller.FORCE_ON_REMOVED:
                self.logger.info("The system is shutting down because the force on plug has been pulled.")
            elif new_state == self.controller.SHALLOW:
                self.logger.info("The system is shutting down because the system has reached the turn-off depth.")
            elif new_state == self.controller.PRESSURE_SW_OPENED:
                self.logger.info("The system is shutting down because the pressure switch has opened.")
            elif new_state == self.controller.LOW_BATT:
                self.logger.info("The system is shutting down due to low battery.")
            elif new_state == self.controller.PC_ERROR:
                self.logger.info("The system is shutting down due to an acquisition software error.")

            #  The controller is telling us to shut down.
            self.logger.info("Initiating a normal shutdown...")

            #  ACK the controller so it knows we're shutting down
            self.controller.sendShutdownAckSignal()

            #  start the shutdown process by calling StopAcquisition. We set the
            #  exit_app keyword to True to exit the app after the cameras have
            #  stopped. We also force the shutdown_on_exit keyword to True since
            #  the controller will cut power to the PC after a minute or so
            #  when in a shutdown state.
            self.StopAcquisition(exit_app=True, shutdown_on_exit=True)

        #  lastly, we update our tracking of the state
        self.controllerCurrentState = new_state


    @QtCore.pyqtSlot(str, str)
    def ControllerError(self, device_name, error):

        if self.controllerStarting:
            #  If there is an error when controllerStarting is set, we know that the
            #  issue is related to opening the serial port and we will assume we
            #  will not be able to use the controller. If we're told to use the
            #  controller and we can't we consider this a fatal error and bail.
            self.logger.critical("Unable to connect to the Camtrawl controller @ port: "+
                self.configuration['controller']['serial_port'] + " baud: " +
                self.configuration['controller']['baud_rate'])
            self.logger.critical("    ERROR: " + error)
            print("Application exiting...")
            QtCore.QCoreApplication.instance().quit()
            return

        #  log the serial error. Normally this will never get called.
        self.logger.error("Camtrawl Controller Serial error: " + error)


    def OpenDatabase(self):
        '''OpenDatabase opens the acquisition database file. This method creates a new
        db file or opens an existing file depending on the mode of operation. It also
        determines the starting image number if running in "combined" mode.
        '''

        # Open the database file
        dbFile = self.log_dir + os.sep + self.configuration['application']['database_name']
        self.logger.info("Opening database file: " + dbFile)

        if not self.db.open(dbFile):
            # If we're running in combined mode and we can't open the db file it is
            # possible that the file is corrupted. When this happens we don't want to
            # fail to acquire so we're going to try to open a new file. We'll just append
            # a number to the original file name so we can easily know the name. On subsequent
            # cycles the original db file will still be corrupt, but this code should
            # either create or open the next non-corrupt file.
            if self.configuration['application']['output_mode'].lower() == 'combined':
                self.logger.error('Error opening SQLite database file ' + dbFile +
                        '. Attempting to open an alternate...')

                #  to make the naming predictable we just append a number to it. MAX_DB_ALTERNATES
                #  sets an upper bound on this process so we don't stall here forever.
                for n_try in range(self.MAX_DB_ALTERNATES):

                    #  create the new filename
                    filename, file_ext = os.path.splitext(dbFile)
                    dbFile = filename + '-' + str(n_try) + file_ext

                    #  try to open it
                    self.logger.info("  Opening database file: " + dbFile)
                    if not self.db.open(dbFile):
                        self.logger.error('  Error opening alternate database file ' + dbFile +'.')
                    else:
                        # success!
                        break

                if not self.db.is_open:
                    #  we failed :(
                    self.logger.error('  Failed to open an alternate database file.')
                    self.logger.error('  Acquisition will continue without the database but ' +
                            'SENSOR DATA WILL NOT BE LOGGED.')
                    self.logger.error('  Will use max(file image number) + 1 to determine ' +
                            'current image number.')
                    self.use_db = False

            else:
                # When we're not running in combined mode, we will always be creating
                # a new db file. If we cannot open a *new* file, we'll assume the
                # file system is not writable and we'll exit the application.
                self.logger.critical('Error opening SQLite database file ' + dbFile +'.')
                self.logger.critical('Unable to continue. Exiting...')
                self.close()

        #  determine the starting image number - if we can't get the number from the
        #  metadata database, we'll pick through the data files.
        if self.use_db:
            self.n_images = self.db.get_next_image_number()
        else:
            #  don't have the db, pick through the files for the next image number.
            #  This is a failsafe for combined mode that allows us to keep acquiring
            #  images even if the metadata database gets corrupted.
            max_num = -1
            cam_dirs = os.listdir(self.image_dir)
            for cam_dir in cam_dirs:
                img_files = os.listdir(self.image_dir + os.sep + cam_dir)
                for file in img_files:
                    try:
                        img_num = int(file.split('_')[0])
                        if (img_num > max_num):
                            max_num = img_num
                    except:
                        pass
            if max_num < 0:
                self.n_images = 1
            else:
                self.n_images = max_num + 1


    def ConfigureCameras(self):
        """
        ConfigureCameras runs through the cameras visible to Spinnaker and configures
        cameras according to the settings in the camera section of the configuration file.
        """
        #  initialize some properties
        self.cameras = []
        self.threads = []
        self.received = {}
        self.this_images = 1
        self.controller_port = {}
        self.hw_triggered_cameras = []
        self.hwTriggered = False

        #  set up the camera interface
        self.system = PySpin.System.GetInstance()
        version = self.system.GetLibraryVersion()
        self.logger.info('Spin library version: %d.%d.%d.%d' % (version.major, version.minor, version.type, version.build))

        # Retrieve list of cameras from the system
        self.logger.info('Getting available cameras...')
        cam_list = self.system.GetCameras()
        self.num_cameras = cam_list.GetSize()

        if (self.num_cameras == 0):
            self.logger.critical("No cameras found!")
            return False
        elif (self.num_cameras == 1):
            self.logger.info('1 camera found.')
        else:
            self.logger.info('%d cameras found.' % self.num_cameras)

        self.logger.info("Configuring cameras:")

        #  work thru the list of discovered cameras
        for cam in cam_list:

            #  create an instance of our spin_camera class
            sc = SpinCamera.spin_camera(cam)

            #  check config data for settings for this camera. This will get config params
            #  for this camera from the config data we read earlier. It will also return
            #  a boolean indicating whether the camera should be utilized or not.
            add_camera, config = self.GetCameraConfiguration(sc.camera_name)

            if add_camera:
                #  we have an entry for this camera so we'll use it
                self.logger.info("  Adding: " + sc.camera_name)

                #  set up the options for saving image data
                image_options = {'file_ext':config['still_image_extension'],
                                 'jpeg_quality':config['jpeg_quality'],
                                 'scale':config['image_scale']}

                #  create the default video profile
                video_profile = CamtrawlAcquisition.DEFAULT_VIDEO_PROFILE

                #  update it with the options from this camera's config
                if config['video_preset'] in self.video_profiles:
                    #  update the video profile dict with the preset values
                    video_profile.update(self.video_profiles[config['video_preset']])

                #  insert the scaling factor into the video profile
                video_profile['scale'] = config['video_scale']

                #  set the video framerate - framerate (in frames/sec) is passed to the
                #  video encoder when recording video files.
                if config['video_force_framerate'] > 0:
                    #  the user has chosen to override the system acquisition rate
                    video_profile['framerate'] = config['video_force_framerate']
                else:
                    #  use the system acquisition rate as the video framerate
                    video_profile['framerate'] = self.configuration['acquisition']['trigger_rate']

                print(video_profile['framerate'])

                #  add or update this camera in the database
                if self.use_db:
                    self.db.update_camera(sc.camera_name, sc.device_info['DeviceID'], sc.camera_id,
                            config['label'], config['rotation'])

                # Set the camera's label
                sc.label = config['label']

                #  set the camera trigger dividers
                sc.save_image_divider = config['save_image_divider']
                sc.trigger_divider = config['trigger_divider']
                self.logger.info('    %s: trigger divider: %d  save image divider: %d' %
                        (sc.camera_name, sc.trigger_divider, sc.save_image_divider))

                #  set up triggering
                if config['trigger_source'].lower() == 'hardware':
                    #  set up the camera to use hardware triggering
                    sc.set_camera_trigger('Hardware')
                    self.logger.info('    %s: Hardware triggering enabled.' % (sc.camera_name))

                    #  if any cameras are hardware triggered we set hwTriggered to True
                    self.hwTriggered = True

                    #  We need to keep a list of hardware triggered cameras so we can store
                    #  some state information about them when triggering. Add this camera
                    #  to the list.
                    self.hw_triggered_cameras.append(sc)

                else:
                    #  set up the camera for software triggering
                    sc.set_camera_trigger('Software')
                    self.logger.info('    %s: Software triggering enabled.' % (sc.camera_name))

                # This should probably be set on the camera to ensure the line is inverted
                # when the camera starts up.
                #ok = sc.set_strobe_trigger(1)

                #  set the camera exposure, gain, and rotation
                sc.set_exposure(config['exposure_us'])
                sc.set_gain(config['gain'])
                sc.rotation = config['rotation']
                self.logger.info('    %s: label: %s  gain: %d  exposure_us: %d  rotation:%s' %
                        (sc.camera_name, config['label'], config['gain'], config['exposure_us'],
                        config['rotation']))

                #  set up HDR if configured
                if config['hdr_enabled']:
                    ok = sc.enable_HDR_mode()
                    if ok:
                        self.logger.info('    %s: Enabling HDR: OK' % (sc.camera_name))
                        if config['hdr_settings'] is not None:
                            self.logger.info('    %s: Setting HDR Params: %s' % (sc.camera_name,
                                    config['hdr_settings']))
                            sc.set_hdr_settings(config['hdr_settings'])
                        else:
                            self.logger.info('    %s: HDR Params not provided. Using values from camera.' %
                                    (sc.camera_name))

                        sc.hdr_save_merged = config['hdr_save_merged']
                        sc.hdr_signal_merged = config['hdr_signal_merged']
                        sc.hdr_merge_method = config['hdr_merge_method']
                        sc.hdr_tonemap_saturation = config['hdr_tonemap_saturation']
                        sc.hdr_tonemap_bias = config['hdr_tonemap_bias']
                        sc.hdr_tonemap_gamma = config['hdr_tonemap_gamma']

                        #  check if there is a camera response file to load
                        if config['hdr_response_file'] in ['none', 'None', 'NONE']:
                            config['hdr_response_file'] = None
                        if config['hdr_response_file'] is not None:
                            try:
                                sc.load_hdr_reponse(config['hdr_response_file'])
                                self.logger.info('    %s: Loaded HDR response file: %s' %
                                        (sc.camera_name, config['hdr_response_file']))
                            except:
                                self.logger.error('    %s: Failed to load HDR response file: %s' %
                                        (sc.camera_name, config['hdr_response_file']))
                    else:
                        self.logger.error('    %s: Failed to enable HDR.' % (sc.camera_name))
                else:
                    sc.disable_HDR_mode()

                #  create a thread for this camera to run in
                thread = QtCore.QThread()
                self.threads.append(thread)

                #  move the camera to that thread
                sc.moveToThread(thread)

                #  connect up our signals
                sc.imageData.connect(self.image_acquired)
                sc.triggerComplete.connect(self.trigger_complete)
                sc.error.connect(self.acq_error)
                sc.acquisitionStarted.connect(self.AcquisitionStarted)
                sc.acquisitionStopped.connect(self.AcquisitionStopped)
                self.trigger.connect(sc.trigger)
                self.stopAcquiring.connect(sc.stop_acquisition)
                self.startAcquiring.connect(sc.start_acquisition)

                if self.configuration['controller']['use_controller']:
                    sc.triggerReady.connect(self.controller.HWTriggerReady)

                #  these signals handle the cleanup when we're done
                sc.acquisitionStopped.connect(thread.quit)
                thread.finished.connect(sc.deleteLater)
                thread.finished.connect(thread.deleteLater)

                #  and start the thread
                thread.start()

                #  add this camera to our list of cameras and set the image
                #  received state to false
                self.cameras.append(sc)
                self.received[sc.camera_name] = False

                #  create a dict to map this camera to it's controller port
                #  This is meaningless if you're not using the camtrawl controller
                self.controller_port[sc] = config['controller_trigger_port']

                #  emit the startAcquiring signal to start the cameras
                self.startAcquiring.emit([sc], self.image_dir, config['save_stills'],
                        image_options, config['save_video'], video_profile)

            else:
                #  There is no default section and no camera specific section
                #  so we skip this camera
                self.logger.info("  Skipped camera: " + sc.camera_name +
                        ". No configuration entry found.")

        #  we're done with setup
        self.logger.info("Camera setup complete.")

        return True


    @QtCore.pyqtSlot()
    def TriggerCameras(self):
        '''
        The TriggerCameras slot is called by the trigger timer and will "trigger"
        the active cameras. This action doesn't directly trigger the cameras. It
        prepares them for triggering but the actual trigger depends on if a camera
        is being hardware or software triggered.

        If a camera is software triggered, it will prepare for and trigger itself
        when this signal is received.

        If a camera is hardware triggered, it will prepare for triggering and then
        emit the "TriggerReady" signal. That signal is connected to this application's
        HWTriggerReady slot which will track the ready state of all hardware triggered
        cameras and when they are all ready, it will call the hardware trigger device's
        trigger method.
        '''

        #  reset the received image state for *all* cameras
        for c in self.cameras:
            self.received[c.camera_name] = False

        #  if any cameras are hardware triggered we have to track some other info
        if self.hwTriggered:
            #  reset the image received state for hardware triggered cameras
            self.ctcTriggerChannel = [False] * len(self.hw_triggered_cameras)
            self.maxExposure = 0
            for c in self.hw_triggered_cameras:
                self.readyToTrigger[c] = False
                self.HWTriggerHDR[c] = False

        #  note the trigger time
        self.trig_time = datetime.datetime.now()

        #  emit the trigger signal to trigger the cameras
        self.trigger.emit([], self.n_images, self.trig_time, True, True)

        # TODO: Currently we only write a single entry in the sensor_data table for
        #       HDR acquisition sequences because we're not incrementing the image
        #       counter for each HDR frame. Since we're not incrementing the number
        #       we don't have a unique key in the sensor_data table for the 3 other
        #       HDR exposures. If we want to change this, the easiest approach would
        #       be to use a decimal notation of image_number.HDR_exposure for the
        #       image numbers. For example, 143.1, 143.2, 143.3, 143.4

        #  and write synced sensor data  to the db
        for sensor_id in self.sensorData:
            for header in self.sensorData[sensor_id]:
                #  check if the data is fresh
                freshness = self.trig_time - self.sensorData[sensor_id][header]['time']
                if freshness.seconds <= self.configuration['sensors']['synchronous_timeout']:
                    #  it is fresh enough. Write it to the db
                    self.db.add_imageinsert_sync_data(self.n_images, sensor_id, header,
                            self.sensorData[sensor_id][header]['data'])


    @QtCore.pyqtSlot(object, list, bool)
    def HWTriggerReady(self, cam, exposure_us, is_HDR):
        '''
        The HWTriggerReady slot is called by each hardware triggered camera when it
        is ready to be triggered. We track the responses and when all cameras are
        ready, we call the CamtrawlController's trigger method which will trigger
        the strobes and the cameras.

        This method is specific to the Camtrawl Controller. If you have a different
        device to trigger your cameras, you'll need to modify this
        '''

        #  update some state info for this camera
        self.readyToTrigger[cam] = True
        self.HWTriggerHDR[cam] = is_HDR

        #  if this camera is set to trigger the exposure will be greater than zero.
        if exposure_us > 0:
            #  update the list that tracks which cameras should be triggered
            #  The controller port numbering starts at 1 so we have to subtract
            #  one when indexing the list.
            self.ctcTriggerChannel[self.controller_port[cam] - 1] = True

        #  track the longest hardware exposure - this ends up being our strobe exposure
        if self.maxExposure < exposure_us:
            self.maxExposure = exposure_us

        #  if all of the HW triggered cameras are ready, we trigger them
        if all(self.readyToTrigger):

            #  strobe pre-fire is the time, in microseconds, that the strobe
            #  trigger signal goes high before the cameras are triggered. This
            #  allows LED strobes to ramp a bit before exposure. Since we want
            #  the total HDR exposure time as short as possible, we disable
            #  strobe pre fire for HDR exposures

            #  disable strobe pre-fire for HDR exposures 2,3 and 4
            if any(self.HWTriggerHDR):
                strobePreFire = 0
            else:
                #  not an HDR trigger so we use the configured pre-fire
                strobePreFire = self.configuration['controller']['strobe_pre_fire']

            #  set the strobe exposures to the longest hardware triggered exposure
            strobe_chan = self.configuration['controller']['strobe_channel']
            if strobe_chan == 1:
                #  only trigger strobe channel 1
                strobe1Exp = self.maxExposure
                #  strobe channel 2 exposure is set to 0 to disable
                strobe2Exp = 0
            elif strobe_chan == 2:
                #  only trigger strobe channel 2
                #  strobe channel 1 exposure is set to 0 to disable
                strobe1Exp = 0
                strobe2Exp = self.maxExposure
            else:
                #  trigger both strobe channels
                strobe1Exp = self.maxExposure
                strobe2Exp = self.maxExposure

            #  call the camtrawl controller's trigger method to trigger the
            #  cameras and strobes.
            self.controller.trigger(strobePreFire, strobe1Exp, strobe2Exp,
                    self.ctcTriggerChannel[0], self.ctcTriggerChannel[1])


    @QtCore.pyqtSlot(str, str, dict)
    def image_acquired(self, cam_name, cam_label, image_data):
        '''image_acquired is called when a camera has acquired an image
        or timed out waiting for one.

        When a camera is in HDR mode this method is called if an exposure
        has the do_signal parameter set to True in the HDR settings.

        '''

        #  Check if we received an image or not
        if (image_data['width'] == 0):
            #  no image data
            log_str = (cam_name + ': FAILED TO ACQUIRE IMAGE')
            if self.use_db:
                self.db.add_dropped(self.n_images, cam_name, self.trig_time)
        else:
            #  we do have image data
            if self.use_db:
                self.db.add_image(self.n_images, cam_name, self.trig_time, image_data['filename'],
                        image_data['exposure'])
            log_str = (cam_name + ': Image Acquired: %dx%d  exp: %d  gain: %2.1f  filename: %s' %
                    (image_data['width'], image_data['height'], image_data['exposure'],
                    image_data['gain'], image_data['filename']))
        self.logger.debug(log_str)

        #  note that this camera has received (or timed out)
        self.received[cam_name] = True


    @QtCore.pyqtSlot(object)
    def trigger_complete(self, cam_obj):
        '''trigger_complete is called when a camera has completed a trigger event.
        '''

        if (all(self.received.values())):

            #  increment our counters
            self.n_images += 1
            self.this_images += 1

            #  check if we're configured for a limited number of triggers
            if ((self.configuration['acquisition']['trigger_limit'] > 0) and
                (self.this_images > self.configuration['acquisition']['trigger_limit'])):
                    #  time to stop acquiring - call our StopAcquisition method and set
                    #  exit_app to True to exit the application after the cameras stop.
                    self.StopAcquisition(exit_app=True,
                            shutdown_on_exit=self.configuration['application']['shut_down_on_exit'])
            else:
                #  keep going - determine elapsed time and set the trigger for the next interval
                elapsed_time_ms = (datetime.datetime.now() - self.trig_time).total_seconds() * 1000
                acq_interval_ms = 1000.0 / self.configuration['acquisition']['trigger_rate']
                next_int_time_ms = int(acq_interval_ms - elapsed_time_ms)
                if next_int_time_ms < 0:
                    next_int_time_ms = 0

                self.logger.debug("Trigger %d. Last interval (ms)=%8.4f  Next trigger (ms)=%8.4f" % (self.this_images,
                        elapsed_time_ms, next_int_time_ms))

                #  start the next trigger timer
                self.triggerTimer.start(next_int_time_ms)


    @QtCore.pyqtSlot(str, str)
    def acq_error(self, cam_name, error_str):

        self.logger.error(cam_name + ':ERROR:' + error_str)


    @QtCore.pyqtSlot(object, str, bool)
    def AcquisitionStarted(self, cam_obj, cam_name, success):
        '''
        The AcquisitionStarted slot is called when a camera responds to the
        startAcquiring signal.
        '''
        if success:
            self.logger.info('    ' + cam_name + ': acquisition started.')
        else:
            self.logger.error('    ' + cam_name + ': unable to start acquisition.')
            #  NEED TO CLOSE THIS CAMERA?


    @QtCore.pyqtSlot(object, str, bool)
    def AcquisitionStopped(self, cam_obj, cam_name, success):
        '''
        The AcquisitionStopped slot is called when a camera responds to the
        stopAcquiring signal. If we're exiting the application, we start the
        process here.
        '''

        if success:
            self.logger.info(cam_name + ': acquisition stopped.')
        else:
            self.logger.error(cam_name + ': unable to stop acquisition.')

        #  update the received dict noting this camera has stopped
        self.received[cam_obj.camera_name] = True

        #  check if all cameras have stopped
        if (all(self.received.values())):
            self.logger.info('All cameras stopped.')

            #  if we're supposed to exit the application, do it
            if self.isExiting:
                self.AcqisitionTeardown()


    def StopAcquisition(self, exit_app=False, shutdown_on_exit=False):

        #  stop the trigger timer if it is running
        self.triggerTimer.stop()

        #  use the received dict to track the camera shutdown. When all
        #  cameras are True, we know all of them have reported that they
        #  have stopped recording.
        for c in self.cameras:
            self.received[c.camera_name] = False

        #  set the exit and shutdown states
        self.isExiting = bool(exit_app)
        self.shutdownOnExit = bool(shutdown_on_exit)

        if self.isAcquiring:
            #  stop the cameras
            self.stopAcquiring.emit([])
        else:
            self.AcqisitionTeardown()

        #  shutdown will continue in the AcquisitionStopped method after
        #  all cameras have stopped.


    def AcqisitionTeardown(self):
        """
        AcqisitionTeardown is called when the application is shutting down.
        The cameras will have already been told to stop acquiring
        """

        #  stop the shutdown delay timer (if it has been started)
        self.shutdownTimer.stop()

        self.logger.info("Camtrawl Acquisition is Stopping...")

        #  stop the controller (if required)
        if self.configuration['controller']['use_controller'] and self.controller is not None:
            self.logger.debug("Closing the controller...")
            self.controller.stopController()

        #  if we're using
        if self.use_db and self.db.is_open:
            self.logger.debug("Closing the database...")
            self.db.close()

        #  we need to make sure we release all references to our SpinCamera
        #  objects so Spinnaker can clean up behind the scenes. If we don't
        #  PySpin.system.ReleaseInstance() raises an error.
        self.logger.debug("Cleaning up references to Spinnaker objects...")
        del self.received
        del self.readyToTrigger
        del self.HWTriggerHDR
        del self.hw_triggered_cameras
        del self.controller_port
        del self.cameras

        #  wait just a bit to allow the Python GC to finish cleaning up.
        delayTimer = QtCore.QTimer(self)
        delayTimer.timeout.connect(self.ApplicationTeardown2)
        delayTimer.setSingleShot(True)
        delayTimer.start(250)


    def ApplicationTeardown2(self):
        '''
        ApplicationTeardown2 is called to finish teardown. This last bit of cleanup
        is triggered by a delay timer to give the Python GC a little time to finish
        cleaning up the references to the Spinnaker camera object.
        '''

        # Now we can release the Spinnaker system instance
        if (self.system):
            self.logger.debug("Releasing Spinnaker system...")
            self.system.ReleaseInstance()
            self.system = None

        #  if we're supposed to shut the PC down on application exit,
        #  get that started here.
        if self.shutdownOnExit:

            self.logger.info("Initiating PC shutdown...")

            #  execute the "shutdown later" command
            if os.name == 'nt':
                #  on windows we can simply call shutdown
                os.system("shutdown -s -t 12")
            else:
                #  on linux we have a script we use to delay the shutdown.
                #  Since the shutdown command can't delay less than one minute,
                #  we use a script to delay 10 seconds and then call shutdown.
                #
                #  You must add an entry in the sudoers file to allow the user running this
                #  application to execute the shutdown command without a password. For example
                #  add these line to your /etc/sudoers file:
                #    camtrawl ALL=NOPASSWD: /usr/local/bin/delay_shutdown.sh
                #    camtrawl ALL=NOPASSWD: /sbin/shutdown.sh
                os.system("sudo /usr/local/bin/delay_shutdown.sh &")


        self.logger.info("Camtrawl Acquisition Stopped.")
        self.logger.info("Application exiting...")

        QtCore.QCoreApplication.instance().quit()


    def GetCameraConfiguration(self, camera_name):
        '''GetCameraConfiguration returns a bool specifying if the camera should
        be utilized and a dict containing any camera configuration parameters. It
        starts with a dict containing the default config files and then updates
        them with options/values specified in the application config file.

        It first looks for camera specific entries, if that isn't found it checks
        for a 'default' entry. If a camera specific entry doesn't exist and there
        is no 'default' section, the camera is not used by the application.
        '''

        add_camera = False

        #  start with the default camera configuration
        config = CamtrawlAcquisition.CAMERA_CONFIG_OPTIONS.copy()

        # Look for a camera specific entry first
        if camera_name in self.configuration['cameras']:
            #  update this camera's config with the camera specific settings
            config = self.__update(config, self.configuration['cameras'][camera_name])
            #  we add cameras that are explicitly configured in the config file
            add_camera = True

        # If that fails, check for a default section
        elif 'default' in self.configuration['cameras']:
            #  update this camera's config with the camera specific settings
            config = self.__update(config, self.configuration['cameras']['default'])
            #  we add all cameras if there is a 'default' section in the config file
            add_camera = True

        return add_camera, config


    def ReadConfig(self, config_file, config_dict):
        '''ReadConfig reads the yaml configuration file and returns the updated
        configuration dictionary.
        '''

        #  read the configuration file
        with open(config_file, 'r') as cf_file:
            try:
                config = yaml.safe_load(cf_file)
            except yaml.YAMLError as exc:
                self.logger.error('Error reading configuration file ' + self.config_file)
                self.logger.error('  Error string:' + str(exc))
                self.logger.error('  We will try to proceed, but things are probably not going to ' +
                        'work like you want them too.')

        # Update/extend the configuration values and return
        return self.__update(config_dict, config)


    def __update(self, d, u):
            """
            Update a nested dictionary or similar mapping.

            Source: https://stackoverflow.com/questions/3232943/update-value-of-a-nested-dictionary-of-varying-depth
            Credit: Alex Martelli / Alex Telon
            """
            for k, v in u.items():
                if isinstance(v, collections.abc.Mapping):
                    #  if a value is None, just assign the value, otherwise keep going
                    if d.get(k, {}) is None:
                        d[k] = v
                    else:
                        d[k] = self.__update(d.get(k, {}), v)
                else:
                    d[k] = v
            return d



def signal_handler(*args):
    '''
    signal_handler is called when ctrl-c is pressed when the python console
    has focus. On Linux this is also called when the terminal window is closed
    or when the Python process gets the SIGTERM signal.
    '''
    print("CTRL-C or SIGTERM/SIGHUP detected. Shutting down...")
    acquisition.StopAcquisition(exit_app=True)


if __name__ == "__main__":
    import sys
    import argparse

    #  set up handlers to trap ctrl-c and (on linux) terminal close events
    #  This allows the user to stop the application with ctrl-c and at
    #  least on linux cleanly shut down when the terminal window is closed.
    #  (Windows does not expose those signals)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    if os.name != 'nt':
        signal.signal(signal.SIGHUP, signal_handler)

    #  set the default application config file path
    config_file = "./CamtrawlAcquisition.yml"
    profiles_file = './VideoProfiles.yml'

    #  parse the command line arguments
    parser = argparse.ArgumentParser(description='CamtrawlAcquisition')
    parser.add_argument("-c", "--config_file", help="Specify the path to the yml configuration file.")
    parser.add_argument("-p", "--profiles_file", help="Specify the path to the yml video profiles definition file.")
    args = parser.parse_args()

    if (args.config_file):
        config_file = os.path.normpath(str(args.config_file))
    if (args.profiles_file):
        profiles_file = os.path.normpath(str(args.profiles_file))

    #  create an instance of QCoreApplication and and instance of the acquisition application
    app = QtCore.QCoreApplication(sys.argv)
    acquisition = CamtrawlAcquisition(config_file=config_file, profiles_file=profiles_file,
            parent=app)

    #  and start the event loop
    sys.exit(app.exec_())



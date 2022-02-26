# coding=utf-8

#     National Oceanic and Atmospheric Administration (NOAA)
#     Alaskan Fisheries Science Center (AFSC)
#     Resource Assessment and Conservation Engineering (RACE)
#     Midwater Assessment and Conservation Engineering (MACE)

#  THIS SOFTWARE AND ITS DOCUMENTATION ARE CONSIDERED TO BE IN THE PUBLIC DOMAIN
#  AND THUS ARE AVAILABLE FOR UNRESTRICTED PUBLIC USE. THEY ARE FURNISHED "AS
#  IS."  THE AUTHORS, THE UNITED STATES GOVERNMENT, ITS INSTRUMENTALITIES,
#  OFFICERS, EMPLOYEES, AND AGENTS MAKE NO WARRANTY, EXPRESS OR IMPLIED,
#  AS TO THE USEFULNESS OF THE SOFTWARE AND DOCUMENTATION FOR ANY PURPOSE.
#  THEY ASSUME NO RESPONSIBILITY (1) FOR THE USE OF THE SOFTWARE AND
#  DOCUMENTATION; OR (2) TO PROVIDE TECHNICAL SUPPORT TO USERS.

"""
.. module:: CamtrawlAcquisition.AcquisitionBase

    :synopsis: Base class for the image acquisition software for the
               Camtrawl underwater stereo camera platform.

| Developed by:  Rick Towler   <rick.towler@noaa.gov>
| National Oceanic and Atmospheric Administration (NOAA)
| National Marine Fisheries Service (NMFS)
| Alaska Fisheries Science Center (AFSC)
| Midwater Assesment and Conservation Engineering Group (MACE)
|
| Author:
|       Rick Towler   <rick.towler@noaa.gov>
| Maintained by:
|       Rick Towler   <rick.towler@noaa.gov>
"""


import os
import sys
import datetime
import logging
import functools
import platform
import subprocess
import collections
import shutil
#  import order seems to matter on linux. QtCore and QtSql (in metadata_db)
#  have to be imported before (I think) cv2. If not you get a weird error
#  loading a shared library when importing them.
from PyQt5 import QtCore
from pathlib import Path
from metadata_db import metadata_db
import google.protobuf
import yaml
import numpy as np
import cv2
import SpinCamera
import PySpin
from CamtrawlServer import CamtrawlServer


class AcquisitionBase(QtCore.QObject):

    # CAMERA_CONFIG_OPTIONS defines the default camera configuration options.
    # These values are used if not specified in the configuration file.
    CAMERA_CONFIG_OPTIONS = {'exposure_us':4000,
                             'gain':18,
                             'label':'Camera',
                             'rotation':'none',
                             'trigger_divider': 1,
                             'sensor_binning': 1,
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
                             'still_image_divider': 1,
                             'jpeg_quality': 90,
                             'image_scale': 100,
                             'save_video': False,
                             'video_preset': 'default',
                             'video_force_framerate': -1,
                             'video_frame_divider': 1,
                             'video_scale': 100}

    #DEFAULT_VIDEO_PROFILE defines the default options for the 'default' video profile.
    DEFAULT_VIDEO_PROFILE = {'encoder':'libx265',
                              'file_ext':'.mp4',
                              'preset':'fast',
                              'crf':26,
                              'pixel_format':'yuv420p',
                              'max_frames_per_file': 1000,
                              'ffmpeg_debug_out': False}

    #  define PyQt Signals
    stopAcquiring = QtCore.pyqtSignal(list)
    startAcquiring = QtCore.pyqtSignal((list, str, bool, dict, bool, dict))
    trigger = QtCore.pyqtSignal(list, int, datetime.datetime, bool, bool)
    stopServer = QtCore.pyqtSignal()

    #  parameterChanged is used to respond to Get and SetParam
    #  requests from CamtrawlServer
    parameterChanged = QtCore.pyqtSignal(str, str, str, bool, str)

    #  specify the application version
    VERSION = '4.0'

    #  specify the maximum number of times the application will attempt to open a
    #  metadata db file when running in combined mode and the original db file
    #  cannot be opened.
    MAX_DB_ALTERNATES = 100

    def __init__(self, config_file=None, profiles_file=None, parent=None):

        super(AcquisitionBase, self).__init__(parent)

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
        self.serverThread = None
        self.server = None
        self.system = None
        self.diskStatTimer = None
        self.cameras = []
        self.threads = []
        self.hw_triggered_cameras = []
        self.received = {}
        self.use_db = True
        self.sensorData = {}
        self.readyToTrigger = {}

        #  create the default configuration dict. These values are used for application
        #  configuration if they are not provided in the config file.
        self.configuration = {}
        self.configuration['application'] = {}
        self.configuration['acquisition'] = {}
        self.configuration['cameras'] = {}
        self.configuration['server'] = {}

        self.configuration['application']['output_mode'] = 'separate'
        self.configuration['application']['output_path'] = './data'
        self.configuration['application']['log_level'] = 'INFO'
        self.configuration['application']['database_name'] = 'CamtrawlMetadata.db3'
        self.configuration['application']['shut_down_on_exit'] = False
        self.configuration['application']['always_trigger_at_start'] = False
        self.configuration['application']['ffmpeg_path'] = ''
        self.configuration['application']['disk_free_monitor'] = True
        self.configuration['application']['disk_free_min_mb'] = 150
        self.configuration['application']['disk_free_check_int_ms'] = 5000

        self.configuration['acquisition']['trigger_rate'] = 5
        self.configuration['acquisition']['trigger_limit'] = -1

        self.configuration['server']['start_server'] = False
        self.configuration['server']['server_port'] = 7889
        self.configuration['server']['server_interface'] = '0.0.0.0'

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
        self.shutdownTimer.setSingleShot(True)

        #  continue the setup after QtCore.QCoreApplication.exec_() is called
        #  by using a timer to call AcquisitionSetup. This ensures that the
        #  application event loop is running when AcquisitionSetup is called.
        startTimer = QtCore.QTimer(self)
        startTimer.timeout.connect(self.AcquisitionSetup)
        startTimer.setSingleShot(True)
        startTimer.start(0)


    def AcquisitionSetup(self):
        '''AcquisitionSetup reads the configuration files, creates the log file,
        opens up the metadata database, and sets up the cameras.
        '''
        #  bump the prompt
        print()

        #  get the application start time
        start_time_string = datetime.datetime.now().strftime("D%Y%m%d-T%H%M%S")

        #  read the configuration file - we start with the default values and
        #  recursively update them with values from the config file in the
        #  ReadConfig method.
        self.configuration = self.ReadConfig(self.config_file, self.configuration)

        #  Do the same thing with the video profiles file. In this case we don't
        #  have any default values and pass in an empty dict.
        self.video_profiles = self.ReadConfig(self.profiles_file, {})

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

        #  create the paths to our logs, images, and settings directories
        self.log_dir = os.path.normpath(self.base_dir + os.sep + 'logs')
        self.image_dir = os.path.normpath(self.base_dir + os.sep + 'images')
        settings_dir = os.path.normpath(self.base_dir + os.sep + 'settings')

        #  set up logging
        try:
            logfile_name = self.log_dir + os.sep + start_time_string + '.log'

            #  make sure we have a directory to log to
            if not os.path.exists(self.log_dir):
                os.makedirs(self.log_dir)

            #  create the logger
            self.logger = logging.getLogger(__name__)
            self.logger.propagate = False
            self.logger.setLevel(self.configuration['application']['log_level'])
            fileHandler = logging.FileHandler(logfile_name)
            formatter = logging.Formatter('%(asctime)s : %(levelname)s - %(message)s')
            fileHandler.setFormatter(formatter)
            self.logger.addHandler(fileHandler)
            consoleLogger = logging.StreamHandler(sys.stdout)
            consoleformatter = logging.Formatter('%(asctime)s : %(message)s')
            consoleLogger.setFormatter(consoleformatter)
            self.logger.addHandler(consoleLogger)

        except:
            #  we failed to open the log file - bail
            print("CRITICAL ERROR: Unable to create log file " + logfile_name)
            print("Application exiting...")
            QtCore.QCoreApplication.instance().quit()
            return

        #  make sure we have a directory to write images to
        try:
            if not os.path.exists(self.image_dir):
                os.makedirs(self.image_dir)
        except:
            #  if we can't create the logging dir we bail
            self.logger.critical("Unable to create image logging directory %s." % self.image_dir)
            self.logger.critical("Application exiting...")
            QtCore.QCoreApplication.instance().quit()
            return

        #  copy the settings files - we do this so we have a copy of the settings
        #  for each deployment.
        try:
            #  make sure we have a settings directory. Assume that if the
            #  settings folder exists, we have already copied the files.
            if not os.path.exists(settings_dir):
                os.makedirs(settings_dir)

                #  copy the settings and profiles files
                shutil.copy2(self.config_file, settings_dir)
                shutil.copy2(self.profiles_file, settings_dir)
        except:
            #  we failed to copy the settings?
            self.logger.warning("Unable to copy settings files to " + settings_dir)

        #  log file is set up and directories created. Get some basic info into the logs
        self.logger.info("Camtrawl Acquisition Starting...")

        try:
            #  set up the camera interface
            self.system = PySpin.System.GetInstance()
        except:
            #  if we can't create the logging dir we bail
            self.logger.critical("Error obtaining PySpin system instance. Have you installed the " +
                    "Spinnaker SDK and PySpin correctly?")
            self.logger.critical("Application exiting...")
            QtCore.QCoreApplication.instance().quit()
            return

        #  report versions
        self.logger.info('Platform: %s %s' % (platform.system(), platform.release()))
        self.logger.info('Python version: %s' % (sys.version))
        self.logger.info('Numpy version: %s' % (np.__version__))
        self.logger.info('OpenCV version: %s' % (cv2.__version__))
        self.logger.info('protobuf version: %s' % (google.protobuf.__version__))
        self.logger.info('PyQt5 version: %s' % (QtCore.QT_VERSION_STR))
        version = self.system.GetLibraryVersion()
        self.logger.info('Spinnaker/PySpin library version: %d.%d.%d.%d' % (version.major,
                version.minor, version.type, version.build))
        self.logger.info("CamtrawlAcquisition version: " + self.VERSION)

        #  note the config files we loaded
        self.logger.info("Configuration file loaded: " + self.config_file)
        self.logger.info("Profiles file loaded: " + self.profiles_file)
        self.logger.info("Logging data to: " + self.base_dir)

        #  set the default_is_synchronous sensor data property
        if self.configuration['sensors']['default_type'].lower in ['synchronous', 'syncd', 'sync']:
            self.default_is_synchronous = True
        else:
            self.default_is_synchronous = False

        #  open/create the image metadata database file
        self.OpenDatabase()

        #  log the acquisition rate and max image count
        self.logger.info("Acquisition Rate: %d images/sec   Max image count: %d" %
                (self.configuration['acquisition']['trigger_rate'],
                self.configuration['acquisition']['trigger_limit']))

        #  check if we should check the available free space on our destination device.
        if self.configuration['application']['disk_free_monitor']:

            #  get the starting free space and report
            disk_stats = shutil.disk_usage(self.image_dir)
            disk_free_mb = disk_stats.free / 1024 / 1024

            #  check if we even have enough space to start
            if disk_free_mb <= self.configuration['application']['disk_free_min_mb']:
                #  no, don't got the space
                self.disk_ok = False
                self.logger.critical("CRITICAL ERROR: Free space: %d MB is less than the " % (disk_free_mb) +
                    "minimum allowed %d MB" % (self.configuration['application']['disk_free_min_mb']))
                self.logger.critical("Application exiting due to lack of free disk space")
            else:
                #  free space is greater than min
                self.disk_ok = True
                self.logger.info("Starting to monitor disk free space. Starting free space: " +
                        "%d MB. Minimum free space set to: %d MB" % (disk_free_mb,
                        self.configuration['application']['disk_free_min_mb']))

                #  Create a timer to periodically check the disk free space
                self.diskStatTimer = QtCore.QTimer(self)
                self.diskStatTimer.timeout.connect(self.CheckDiskFreeSpace)
                self.diskStatTimer.setSingleShot(False)
                self.diskStatTimer.start(self.configuration['application']['disk_free_check_int_ms'])
        else:
            #  we're not checking the disk free space
            self.disk_ok = True

        #  if the free space is ok, configure the cameras
        if self.disk_ok:
            self.cam_ok = self.ConfigureCameras()
            if not self.cam_ok:
                #  we were unable to find any cameras
                self.logger.critical("CRITICAL ERROR: Unable to find any cameras. " +
                        "The application will exit.")
        else:
            self.cam_ok = False


        #  At this point we should know if we have at least one camera to use
        #  and some free disk space but what we do next may depend on how the
        #  child class is implemented. We'll finish setup in another method so
        #  we can override it as needed in child classes to handle their unique
        #  setup requirements.
        #
        #  If we have at least one camera available we delay calling this method
        #  to give the cameras time to start. This really just makes the log linear
        #  since the camera threads will log their start before anything is logged
        #  in AcquisitionSetup2. This makes the log more readable.
        setupDelayTimer = QtCore.QTimer(self)
        setupDelayTimer.timeout.connect(self.AcquisitionSetup2)
        setupDelayTimer.setSingleShot(True)
        if self.cam_ok:
            setupDelayTimer.start(250)
        else:
            setupDelayTimer.start(0)


    def AcquisitionSetup2(self):
        '''
        AcquisitionSetup2 handles the last details of startup and what to do
        if we have a problem with the cams or disk.
        '''

        #  check if everything is ok
        if self.cam_ok and self.disk_ok:
            #  the cameras are ready to acquire. Set the isAcquiring property
            self.isAcquiring = True

            #  start the server, if enabled
            if self.configuration['server']['start_server']:
                self.StartServer()

            #  start the trigger timer. Set a long initial interval
            #  to allow the cameras time to finish getting ready.
            self.triggerTimer.start(500)

        else:
            #  no, something didn't work out so check if we're supposed to shut down.
            #  If so, we will delay the shutdown to allow the user to exit the app
            #  before the PC shuts down and correct the problem.
            if self.configuration['application']['shut_down_on_exit']:
                self.logger.error("Shutdown on exit is set. The PC will shut down in 5 minutes.")
                self.logger.error("You can exit the application by pressing CTRL-C to " +
                        "circumvent the shutdown and keep the PC running.")

                #  set the shutdownOnExit attribute so we, er shutdown on exit
                self.shutdownOnExit = True

                #  complete setup of our shutdown timer and start it
                self.shutdownTimer.timeout.connect(self.AcqisitionTeardown)
                #  delay shutdown for 5 minutes
                self.shutdownTimer.start(5000 * 60)

            else:
                #  Stop acquisition and close the app
                self.StopAcquisition(exit_app=True, shutdown_on_exit=False)


    @QtCore.pyqtSlot()
    def CheckDiskFreeSpace(self):
        '''
        CheckDiskFreeSpace checks the available free space for the data directory and
        stops acquisition if it drops below the min threshold.
        '''

        #  get the disk free space in MB
        disk_stats = shutil.disk_usage(self.image_dir)
        disk_free_mb = disk_stats.free / 1024 / 1024

        if disk_free_mb <= self.configuration['application']['disk_free_min_mb']:

            #  stop the timer
            self.diskStatTimer.stop()

            #  Log that we're stopping because we're out of disk space
            self.logger.critical("The system is stopping because the data disk is full.")
            self.logger.critical("  Free space: %d MB is less than the " % (disk_free_mb) +
                    "minimum allowed %d MB" % (self.configuration['application']['disk_free_min_mb']))

            #  Stop acquisition and close the app
            self.StopAcquisition(exit_app=True,
                    shutdown_on_exit=self.configuration['application']['shut_down_on_exit'])


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

        # Retrieve list of cameras from the system
        self.logger.info('Getting available cameras...')
        cam_list = self.system.GetCameras()
        self.num_cameras = cam_list.GetSize()

        if (self.num_cameras == 0):
            self.logger.critical("No cameras found!")
            return False
        elif (self.num_cameras == 1):
            s = 'camera'
            self.logger.info('1 camera found.')
        else:
            s = 'cameras'
            self.logger.info('%d cameras found.' % self.num_cameras)

        self.logger.info("Configuring " + s + ":")

        #  work thru the list of discovered cameras
        for cam in cam_list:

            #  create an instance of our spin_camera class
            sc = SpinCamera.SpinCamera(cam)

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
                video_profile = AcquisitionBase.DEFAULT_VIDEO_PROFILE

                #  update it with the options from this camera's config
                if config['video_preset'] in self.video_profiles:
                    #  update the video profile dict with the preset values
                    #video_profile.update(self.video_profiles[config['video_preset']])
                    video_profile = self.video_profiles[config['video_preset']]

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

                #  insert the ffmpeg path to the video profile. Convert relative paths to
                #  absolute. Empty/None assumes ffmpeg is on the system path
                if self.configuration['application']['ffmpeg_path'] in [None, '']:
                    video_profile['ffmpeg_path'] = None
                else:
                    if self.configuration['application']['ffmpeg_path'][0:1] in ['./', '.\\']:
                        #  get the directory containing this script
                        ffpath = functools.reduce(lambda l,r: l + os.path.sep + r,
                                os.path.dirname(os.path.realpath(__file__)).split(os.path.sep))
                    else:
                        ffpath = self.configuration['application']['ffmpeg_path']
                    video_profile['ffmpeg_path'] = os.path.normpath(ffpath)

                #  add or update this camera in the database
                if self.use_db:
                    self.db.update_camera(sc.camera_name, sc.device_info['DeviceID'], sc.camera_id,
                            config['label'], config['rotation'], sc.device_info['DeviceVersion'],
                            sc.device_info['DeviceCurrentSpeed'])

                # Set the camera's label
                sc.label = config['label']

                #  set the camera trigger and saving dividers
                sc.save_stills_divider = config['still_image_divider']
                sc.save_video_divider = config['video_frame_divider']
                sc.trigger_divider = config['trigger_divider']
                self.logger.info(('    %s: trigger divider: %d  save image divider: %d' +
                        '  save frame divider: %d') % (sc.camera_name, sc.trigger_divider,
                        sc.save_stills_divider, sc.save_video_divider))

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
                        (sc.camera_name, config['label'], sc.get_gain(), sc.get_exposure(),
                        config['rotation']))

                #  set the sensor binning
                sc.set_binning(config['sensor_binning'])
                binning = sc.get_binning()
                self.logger.info('    %s: Sensor binning set to %i x %i' %
                        (sc.camera_name, binning, binning))

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
                sc.imageData.connect(self.CamImageAcquired)
                sc.triggerComplete.connect(self.CamTriggerComplete)
                sc.error.connect(self.LogCamError)
                sc.cameraDebug.connect(self.LogCamDebug)
                sc.acquisitionStarted.connect(self.AcquisitionStarted)
                sc.acquisitionStopped.connect(self.AcquisitionStopped)
                self.trigger.connect(sc.trigger)
                self.stopAcquiring.connect(sc.stop_acquisition)
                self.startAcquiring.connect(sc.start_acquisition)

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

                if config['save_stills']:
                    self.logger.info('    %s: Saving stills as %s  Scale: %i' % (sc.camera_name,
                            image_options['file_ext'], image_options['scale']))

                if config['save_video']:
                    self.logger.info('    %s: Saving video as %s  Video profile: %s' % (sc.camera_name,
                            video_profile['file_ext'], config['video_preset']))

                #  issue a warning if a camera is not saving any image data
                if config['save_video'] or config['save_stills']:
                    self.logger.info('    %s: Image data will be written to: %s' % (sc.camera_name,
                                self.image_dir + os.sep + sc.camera_name))
                else:
                    self.logger.warning('    %s: WARNING: Both video and still saving is disabled. ' %
                            (sc.camera_name) + 'NO IMAGE DATA WILL BE RECORDED')

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

        #  we return true if we found at least 1 camera
        if len(self.cameras) > 0:
            return True
        else:
            return False


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
        emit the "TriggerReady" signal. You must connect these signals to a slot in
        your application that tracks the ready cameras and triggers them when all
        triggered cameras are ready.
        '''

        #  reset the received image state for *all* cameras
        for c in self.cameras:
            self.received[c.camera_name] = False

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
                    self.db.insert_sync_data(self.n_images, self.sensorData[sensor_id][header]['time'],
                            sensor_id, header, self.sensorData[sensor_id][header]['data'])


    @QtCore.pyqtSlot(str, str, dict)
    def CamImageAcquired(self, cam_name, cam_label, image_data):
        '''CamImageAcquired is called when a camera has acquired an image
        or timed out waiting for one.

        When a camera is in HDR mode this method is called if an exposure
        has the emit_signal parameter set to True in the HDR settings.
        '''

        #  Check if we received an image or not
        if (image_data['width'] == 0):
            #  no image data
            log_str = (cam_name + ': FAILED TO ACQUIRE IMAGE')
            if self.use_db:
                self.db.add_dropped(self.n_images, cam_name, self.trig_time)
        else:
            #  we do have image data - we log this image to the images table

            #  Only store the image file name, no path info
            filename = Path(image_data['filename']).name

            if self.use_db:
                self.db.add_image(self.n_images, cam_name, self.trig_time, filename,
                        image_data['exposure'], image_data['gain'])
            log_str = (cam_name + ': Image Acquired: %dx%d  exp: %d  gain: %2.1f  filename: %s' %
                    (image_data['width'], image_data['height'], image_data['exposure'],
                    image_data['gain'], filename))
        self.logger.debug(log_str)

        #  note that this camera has received (or timed out)
        self.received[cam_name] = True


    @QtCore.pyqtSlot(object)
    def CamTriggerComplete(self, cam_obj):
        '''CamTriggerComplete is called when a camera has completed a trigger event.
        '''

        #  check if all triggered cameras have completed the trigger sequence
        if (all(self.received.values())):
            #  all cameras are done. Increment our counters
            self.n_images += 1
            self.this_images += 1

            #  check if we're configured for a limited number of triggers
            if ((self.configuration['acquisition']['trigger_limit'] > 0) and
                (self.this_images > self.configuration['acquisition']['trigger_limit'])):

                    self.logger.info("Trigger limit of %i triggers reached. Shutting down..." %
                            (self.this_images-1))

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

                self.logger.debug("Trigger %d. Last interval (ms)=%8.4f  Next trigger (ms)=%8.4f" %
                        (self.this_images, elapsed_time_ms, next_int_time_ms))

                #  start the next trigger timer
                self.triggerTimer.start(next_int_time_ms)


    @QtCore.pyqtSlot(str, str)
    def LogCamError(self, cam_name, error_str):
        '''
        The LogCamError slot is called when a camera runs into an error. For now
        we just log the error and move on.
        '''
        #  log it.
        self.logger.error(cam_name + ':ERROR:' + error_str)


    @QtCore.pyqtSlot(str, str)
    def LogCamDebug(self, cam_name, debug_str):
        '''
        The LogCamDebug slot is called when a camera emits some debug info. For now
        we just log the error and move on.
        '''
        #  log it.
        self.logger.debug(cam_name + ':DEBUG:' + debug_str)


    @QtCore.pyqtSlot(str)
    def LogServerError(self, error_str):
        '''
        The LogServerError slot is called when a CamtrawlServer runs into an error.
        For now we just log the error and move on.
        '''
        #  log it.
        self.logger.error('CamtrawlServer:ERROR:' + error_str)


    @QtCore.pyqtSlot(object, str, bool)
    def AcquisitionStarted(self, cam_obj, cam_name, success):
        '''
        The AcquisitionStarted slot is called when a camera responds to the
        startAcquiring signal.
        '''
        if success:
            self.logger.info(cam_name + ': acquisition started.')
        else:
            self.logger.error(cam_name + ': unable to start acquisition.')
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
        '''
        StopAcquisition, starts the process of stopping image acquisition. This method
        updates a few properties and then emits the stopAcquiring signal which informs
        the cameras to stop acquiring and close.

        The process of stopping then continues in AcquisitionStopped when all cameras
        have responded to the stopAcquiring signal.
        '''

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

        self.logger.info("Acquisition is Stopping...")

        #  if we're using the database, close it
        if self.use_db and self.db.is_open:
            self.logger.debug("Closing the database...")
            self.db.close()

        #  same with the server
        if self.configuration['server']['start_server']:
            self.logger.debug("Closing the server...")
            self.stopServer.emit()

        #  we need to make sure we release all references to our SpinCamera
        #  objects so Spinnaker can clean up behind the scenes. If we don't
        #  PySpin.system.ReleaseInstance() raises an error.
        self.logger.debug("Cleaning up references to Spinnaker objects...")
        del self.received
        del self.hw_triggered_cameras
        del self.cameras

        #  wait just a bit to allow the Python GC to finish cleaning up.
        delayTimer = QtCore.QTimer(self)
        delayTimer.timeout.connect(self.AcqisitionTeardown2)
        delayTimer.setSingleShot(True)
        delayTimer.start(500)


    def AcqisitionTeardown2(self):
        '''
        AcqisitionTeardown2 is called to finish teardown. This last bit of cleanup
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
                #  on windows we can simply call shutdown and delay 12 seconds
                subprocess.Popen(["shutdown", "-s", "-t", "12"],
                        creationflags=subprocess.DETACHED_PROCESS |
                        subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                #  on linux we have a script we use to delay the shutdown.
                #  Since the shutdown command can't delay less than one minute,
                #  we use a script to delay 5 seconds and then call shutdown.
                #
                #  You must add an entry in the sudoers file to allow the user running this
                #  application to execute the shutdown command without a password. For example
                #  add these lines to your /etc/sudoers file:
                #    camtrawl ALL=NOPASSWD: /camtrawl/scripts/delay_shutdown.sh
                #    camtrawl ALL=NOPASSWD: /sbin/shutdown.sh
                subprocess.Popen(['sudo', '/camtrawl/scripts/delay_shutdown.sh'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        preexec_fn=os.setpgrp)

        self.logger.info("Acquisition Stopped.")
        self.logger.info("Application exiting...")

        #  we be done
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
        config = AcquisitionBase.CAMERA_CONFIG_OPTIONS.copy()

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


    def StartServer(self):
        '''StartServer will start the CamtrawlServer. The Camtrawl server provides
        a command and control interface and serves up image and sensor data on the
        network. It can be used in conjunction with the Camtrawl client in applications
        for remote viewing and control of the system.
        '''

        self.logger.info("Opening Camtrawl server on  " +
                self.configuration['server']['server_interface'] + ":" +
                str(self.configuration['server']['server_port']))

        #  create an instance of CamtrawlServer
        self.server = CamtrawlServer.CamtrawlServer(
                self.configuration['server']['server_interface'],
                self.configuration['server']['server_port'])

        #  connect the server's signals. Connect both the sync and async signals
        #  to the same method since we use our configuration file to figure out
        #  how to log sensor data.
        self.server.sensorData.connect(self.SensorDataAvailable)
        self.server.getParameterRequest.connect(self.GetParameterRequest)
        self.server.setParameterRequest.connect(self.SetParameterRequest)
        self.server.error.connect(self.LogServerError)
        self.stopServer.connect(self.server.stopServer)

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


    def OpenDatabase(self):
        '''OpenDatabase opens the acquisition database file. This method creates a new
        db file or opens an existing file depending on the mode of operation. It also
        determines the starting image number if running in "combined" mode.

        When logging data in "combined" mode (all data in one folder, which also means
        all metadata in one sqlite file) this method will attempt to create a new sqlite
        file if the initial file becomes corrupted. While this is unlikely, we don't
        want to fail to acquire because of a bad sqlite file.
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
                            'this situation is not ideal.')
                    self.logger.error('  Will use max(file image number) + 1 to determine ' +
                            'current image number.')
                    self.use_db = False

            else:
                # When we're not running in combined mode, we will always be creating
                # a new db file. If we cannot open a *new* file, we'll assume the
                # file system is not writable and we'll exit the application.
                self.logger.error('Error opening SQLite database file ' + dbFile +'.')
                self.logger.error('  Acquisition will continue without the database but ' +
                            'this situation is not ideal.')
                self.use_db = False

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


    @QtCore.pyqtSlot(str, str)
    def GetParameterRequest(self, module, parameter):

        pass

    @QtCore.pyqtSlot(str, str, str)
    def SetParameterRequest(self, module, parameter, value):

        pass


    @QtCore.pyqtSlot(str, str, datetime.datetime, str)
    def SensorDataAvailable(self, sensor_id, header, rx_time, data):
        '''
        The SensorDataAvailable slot is called when sensor data is received.


        CamtrawlAcquisition lumps sensor data into 2 groups. Synced sensor data
        is cached when received and then logged to the database when the cameras
        are triggered and the data are linked to the image. Async sensor data is
        logged immediately and is not linked to any image. You configure sensor
        specifics in the "sensor" section of the configuration file.

        Args:
            sensor_id (str): A unique string defining the sensor. Sensors can
                             have multiple data types, each defined by a unique
                             header.
            header (str): A string specifying the datagram header of this datagram.

            rx_time (datetime): A datetime object defining the time the data was
                                received or created by the producer.
            data (str): A string containing the sensor data. The string is
                        assumed to be in the form:
                          <header>,<data>

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
            self.sensorData[sensor_id][header] = {'time':rx_time, 'data':data}

        else:
            #  this is async sensor data so we just write it
            if self.use_db:
                self.db.insert_async_data(sensor_id, header, rx_time, data)


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

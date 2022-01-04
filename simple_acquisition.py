"""
simple_acquisition.py is a

Rick Towler
MACE Group
NOAA Alaska Fisheries Science Center

"""

from PyQt5 import QtCore
import logging
import collections
import datetime
import os
import yaml
import spin_camera
import PySpin
from metadata_db import metadata_db


class simple_acquisition(QtCore.QObject):

    # CAMERA_CONFIG_OPTIONS defines the default camera configuration options.
    # These values are used if not specified in the configuration file.
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
    stop_acq = QtCore.pyqtSignal(list)
    start_acq = QtCore.pyqtSignal(list, str, str, dict)
    trigger = QtCore.pyqtSignal(list, int, datetime.datetime, bool, bool)

    def __init__(self, config_file, parent=None):

        super(simple_acquisition, self).__init__(parent)

        #  Set the configuration file path if provided
        if config_file:
            self.config_file = config_file
        else:
            self.config_file = './simple_acquisition.yml'
        self.config_file = os.path.normpath(self.config_file)

        # Define default properties
        self.system = None
        self.cameras = []
        self.threads = []
        self.received = {}
        self.use_db = True

        #  create the default configuration dict. These values are used for application
        #  configuration if they are not provided in the config file.
        self.configuration = {}
        self.configuration['application'] = {}
        self.configuration['acquisition'] = {}
        self.configuration['cameras'] = {}

        self.configuration['application']['output_mode'] = 'separate'
        self.configuration['application']['output_path'] = './data'
        self.configuration['application']['log_level'] = 'DEBUG'
        self.configuration['application']['database_name'] = 'CamtrawlMetadata.db3'
        self.configuration['application']['shut_down_on_exit'] = True
        self.configuration['application']['always_trigger_at_start'] = False

        self.configuration['acquisition']['trigger_rate'] = 5
        self.configuration['acquisition']['trigger_limit'] = -1
        self.configuration['acquisition']['save_stills'] = True
        self.configuration['acquisition']['still_image_extension'] = '.jpg'
        self.configuration['acquisition']['jpeg_quality'] = 90
        self.configuration['acquisition']['image_scale'] = 100
        self.configuration['acquisition']['save_video'] = False
        self.configuration['acquisition']['video_preset'] = 'default'
        self.configuration['acquisition']['video_scale'] = 100

        #  some misc constants
        self.log_level = logging.ERROR
        self.database_filename = 'CamTrawlMetadata.db3'

        #  connect the QCoreApplication aboutToQuit signal to our acq_shutdown
        #  slot to ensure that we clean up on exit
        parent.aboutToQuit.connect(self.acq_shutdown)

        #  Create an instance of metadata_db which is a simple interface to the
        #  camtrawl metadata database
        self.db = metadata_db()

        startTimer = QtCore.QTimer(self)
        startTimer.timeout.connect(self.acquisition_setup)
        startTimer.setSingleShot(True)
        startTimer.start(0)


    def acquisition_setup(self):

        #  get the application start time
        start_time_string = datetime.datetime.now().strftime("D%Y%m%d-T%H%M%S")

        #  read the configuration file - we start with the default values and
        #  recursively update them with values from the config file in the
        #  ReadConfig method.
        self.configuration = self.ReadConfig(self.config_file, self.configuration)

        #  Do the same thing with the video profiles file
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

        #  create the paths to our logs and images directories
        self.log_dir = os.path.normpath(self.base_dir + os.sep + 'logs')
        self.image_dir = os.path.normpath(self.base_dir + os.sep + 'images')

        #  set up logging
        if 'log_level' in self.configuration['application']:
            self.log_level = self.configuration['application']['log_level']
        try:
            logfile_name = self.log_dir + os.sep + start_time_string + '.log'

            #  make sure we have a directory to log into
            if not os.path.exists(self.log_dir):
                os.makedirs(self.log_dir)

            #  create the logger
            self.logger = logging.getLogger(__name__)
            self.logger.setLevel(self.log_level)
            fileHandler = logging.FileHandler(logfile_name)
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            fileHandler.setFormatter(formatter)
            self.logger.addHandler(fileHandler)
            consoleLogger = logging.StreamHandler(sys.stdout)
            self.logger.addHandler(consoleLogger)

        except:
            print("CRITICAL ERROR: Unable to create log file " + logfile_name)
            self.close()

        #  make sure we have a directory to write images into
        try:
            if not os.path.exists(self.image_dir):
                os.makedirs(self.image_dir)
        except:
            self.logger.critical("Unable to create image logging directory %s. Exiting..." % self.image_dir)
            self.close()

        # Open the database file
        dbFile = self.log_dir + os.sep + self.database_filename
        self.logger.info("Opening database file: " + dbFile)
        if not self.db.open(dbFile):
            # If we're running in combined mode and we can't open the db file it is
            # possible that the file is corrupted. When this happens we don't want to
            # fail to acquire, but rather continue on as much as possible.
            if self.configuration['application']['output_mode'].lower() == 'combined':
                self.logger.error('Error opening SQLite database file ' + dbFile +'.')
                self.logger.error('  Will use max(file image number) + 1 to determine current image number.')
                self.use_db = False
            else:
                # When we're not running in combined mode, we will always be creating
                # a new db file. If we cannot open a *new* file, we'll assume the
                # filesystem is not writable and we'll exit the application.
                self.logger.critical('Error opening SQLite database file ' + dbFile +'.')
                self.logger.critical('Unable to continue. Exiting...')
                self.close()

        #  Now that we're all set up, start acquisition
        self.acquisition_start()


    def acquisition_start(self):
        """

        """
        #  initialize some properties
        self.cameras = []
        self.threads = []
        self.received = {}
        self.this_images = 1

        self.logger.info("Starting acquisition")

        #  set up the camera interface
        self.system = PySpin.System.GetInstance()
        version = self.system.GetLibraryVersion()
        self.logger.info('Spin library version: %d.%d.%d.%d' % (version.major, version.minor, version.type, version.build))

        # Retrieve list of cameras from the system
        cam_list = self.system.GetCameras()
        self.num_cameras = cam_list.GetSize()

        if (self.num_cameras == 0):
            self.logger.critical("No cameras found. Exiting...")
            self.close()
        elif (self.num_cameras == 1):
            self.logger.info('1 camera found.')
        else:
            self.logger.info('%d cameras found.' % self.num_cameras)

        self.logger.info("Configuring cameras:")

        #  work thru the list of discovered cameras
        for cam in cam_list:

            #  Set defaults in case parameters are missing in config file. This ensures
            #  we have something to work with below.
            config = dict(CAMERA_CONFIG_OPTIONS)

            #  create an instance of our spin_camera class
            sc = spin_camera.spin_camera(cam)

            #  check config data for settings for this camera. This will get config params
            #  for this camera from the config data we read earlier. It will also return
            #  a boolean indicating whether the camera should be utilized or not.
            add_camera, new_configs = self.get_camera_configuration(sc.camera_name)

            if add_camera:
                #  we have an entry for this camera so we'll use it
                self.logger.info("  Adding: " + sc.camera_name)

                #  update the default camera setings with values from config file
                config.update(new_configs)

                #  add or update this camera in the database
                if self.use_db:
                    self.db.update_camera(sc.camera_name, sc.device_info['DeviceID'], sc.camera_id,
                            config['label'], config['rotation'])

                #  set the camera to use software triggering
                sc.set_camera_trigger('Software')

                # This should probably be set on the camera to ensure the line is inverted
                # when the camera starts up.
                #ok = sc.set_strobe_trigger(1)

                # Set the camera's label
                sc.label = config['label']

                #  set the camera exposure, gain, and rotation
                sc.set_exposure(config['exposure_us'])
                sc.set_gain(config['gain'])
                sc.rotation = config['rotation']
                self.logger.info('    %s: label: %s  gain: %d  exposure_us: %d  rotation:%s' %
                        (sc.camera_name, config['label'], config['gain'], config['exposure_us'], config['rotation']))

                #  set up HDR if configured
                if config['hdr_enabled']:
                    ok = sc.enable_HDR_mode()
                    if ok:
                        self.logger.info('    %s: Enabling HDR: OK' % (sc.camera_name))
                        if config['hdr_settings'] is not None:
                            self.logger.info('    %s: Setting HDR Params: %s' % (sc.camera_name, config['hdr_settings']))
                            sc.set_hdr_settings(config['hdr_settings'])
                        else:
                            self.logger.info('    %s: HDR Params not provided. Using values from camera.' % (sc.camera_name))

                        sc.hdr_save_merged = config['hdr_save_merged']
                        sc.hdr_signal_merged = config['hdr_signal_merged']
                        sc.hdr_merge_method = config['hdr_merge_method']
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
                sc.acquisitionStarted.connect(self.acq_started)
                sc.acquisitionStopped.connect(self.acq_stopped)
                self.trigger.connect(sc.trigger)
                self.stop_acq.connect(sc.stop_acquisition)
                self.start_acq.connect(sc.start_acquisition)

                #  these signals handle the cleanup when we're done
                sc.acquisitionStopped.connect(thread.quit)
                thread.finished.connect(sc.deleteLater)
                thread.finished.connect(thread.deleteLater)

                #  and start the thread
                thread.start()

                #  add this camera to our list of cameras and set the image
                #  received state to false
                self.cameras.append(sc)
                self.received[sc] = False
            else:
                #  There is no default section and no camera specfic section
                #  so we skip this camera
                self.logger.info("  Skipped camera: " + sc.camera_name + ". No configuration entry found.")

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

        #  start the cameras
        self.logger.info("Starting camera acquisition.")
        self.start_acq.emit([], self.image_dir, self.output_type, self.record_options)

        self.image_timer = QtCore.QTimer(self)
        self.image_timer.timeout.connect(self.trigger_cams)
        self.image_timer.setSingleShot(True)

        cam_list.Clear()

        #  we have to wait just a bit before starting triggering to allow
        #  the cameras to get ready
        self.image_timer.start(500)


    def trigger_cams(self):
        '''trigger_cams will trigger
        '''

        #  reset the image received state of all cameras
        for c in self.cameras:
            self.received[c] = False

        #  note the trigger time
        self.trig_time = datetime.datetime.now()

        #  emit the trigger signal to trigger the cameras
        self.trigger.emit([], self.n_images, self.trig_time, True, True)


    @QtCore.pyqtSlot(object, str, dict)
    def image_acquired(self, cam_obj, cam_name, image_data):
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
            log_str = (cam_name + ': Image Acquired: %dx%d  exp: %d  gain: %d  filename: %s' %
                    (image_data['width'], image_data['height'], image_data['exposure'],
                    image_data['gain'], image_data['filename']))
        self.logger.debug(log_str)

        #  note that this camera has received (or timed out)
        self.received[cam_obj] = True


    @QtCore.pyqtSlot(object)
    def trigger_complete(self, cam_obj):
        '''trigger_complete is called when a camera has completed a trigger event.
        '''

        if (all(self.received.values())):

            #  increment our counters
            self.n_images += 1
            self.this_images += 1

            if (self.this_images > self.acq_n_images):
                #  time to stop acquiring
                self.image_timer.stop()

                for c in self.cameras:
                    self.received[c] = False

                #  stop the cameras
                self.stop_acq.emit([])

            else:
                #  keep going - determine elapsed time and set the trigger for
                #  the next interval
                elapsed_time_ms = (datetime.datetime.now() - self.trig_time).total_seconds() * 1000
                next_int_time_ms = self.acq_interval_ms - elapsed_time_ms
                if next_int_time_ms < 0:
                    next_int_time_ms = 0

                self.logger.debug("Trigger %d. Last interval (ms)=%8.4f  Next trigger (ms)=%8.4f" % (self.this_images,
                        elapsed_time_ms, next_int_time_ms))

                self.image_timer.start(next_int_time_ms)


    def acq_error(self, cam_name, error_str):

        self.logger.error(cam_name + ':ERROR:' + error_str)


    def acq_started(self, cam_obj, cam_name, success):
        if success:
            self.logger.info(cam_name + ': acquisition started.')
        else:
            self.logger.error(cam_name + ': unable to start acquisition.')

            #  NEED TO CLOSE THIS CAMERA


    def acq_stopped(self, cam_obj, cam_name, success):

        if success:
            self.logger.info(cam_name + ': acquisition stopped.')
        else:
            self.logger.error(cam_name + ': unable to stop acquisition.')

        self.received[cam_obj] = True
        if (all(self.received.values())):
            self.logger.info('All cameras stopped. Shutting down.')
            QtCore.QCoreApplication.instance().quit()


    def acq_shutdown(self):
        """
        acq_shutdown is called when the application is closed and performs some
        basic clean up
        """

        if self.use_db and self.db.is_open:
            self.db.close()

        print("Cleaning up.")
        del self.cameras
        del self.received

        # Release system instance
        if (self.system):
            self.system.ReleaseInstance()
            self.system = None

        print("Done.")


    def get_camera_configuration(self, camera_name):
        '''get_camera_configuration returns a bool specifying if the camera should
        be utilized and a dict containing any camera configuration parameters.

        It first looks for camera specific entries, if that isn't found it checks
        for a 'default' entry.
        '''

        add_camera = False
        config = {}

        # Look for a camera specific entry first
        if camera_name in self.configuration['cameras']:
            for option in CAMERA_CONFIG_OPTIONS:
                if option in self.configuration['cameras'][camera_name].keys():
                    config[option] = self.configuration['cameras'][camera_name][option]
            add_camera = True

        # If that fails, check for a default section
        elif 'default' in self.configuration['cameras']:
            # Default section is available. Use parameters from default
            for option in CAMERA_CONFIG_OPTIONS:
                if option in self.configuration['cameras']['default'].keys():
                    config[option] = self.configuration['cameras']['default'][option]
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


if __name__ == "__main__":
    import sys
    import argparse

    config_file = "./simple_acquisition.yml"

    parser = argparse.ArgumentParser(description='simple_acquisition')

    parser.add_argument("-c", "--config_file", help="Specify the path to the yml configuration file.")

    args = parser.parse_args()

    if (args.config_file):
        config_file = os.path.normpath(str(args.config_file))

    app = QtCore.QCoreApplication(sys.argv)
    form = simple_acquisition(config_file, parent=app)
    sys.exit(app.exec_())



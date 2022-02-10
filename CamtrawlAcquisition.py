#!/usr/bin/env python3
# coding=utf-8
#
#     National Oceanic and Atmospheric Administration (NOAA)
#     Alaskan Fisheries Science Center (AFSC)
#     Resource Assessment and Conservation Engineering (RACE)
#     Midwater Assessment and Conservation Engineering (MACE)
#
#  THIS SOFTWARE AND ITS DOCUMENTATION ARE CONSIDERED TO BE IN THE PUBLIC DOMAIN
#  AND THUS ARE AVAILABLE FOR UNRESTRICTED PUBLIC USE. THEY ARE FURNISHED "AS
#  IS."  THE AUTHORS, THE UNITED STATES GOVERNMENT, ITS INSTRUMENTALITIES,
#  OFFICERS, EMPLOYEES, AND AGENTS MAKE NO WARRANTY, EXPRESS OR IMPLIED,
#  AS TO THE USEFULNESS OF THE SOFTWARE AND DOCUMENTATION FOR ANY PURPOSE.
#  THEY ASSUME NO RESPONSIBILITY (1) FOR THE USE OF THE SOFTWARE AND
#  DOCUMENTATION; OR (2) TO PROVIDE TECHNICAL SUPPORT TO USERS.
#
"""
.. module:: CamtrawlAcquisition.CamtrawlAcquisition

    :synopsis: CamtrawlAcquisition is the main application that provides
               image acquisition, sensor logging, and control of the
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
from AcquisitionBase import AcquisitionBase
from PyQt5 import QtCore
import CamtrawlController


class CamtrawlAcquisition(AcquisitionBase):
    """
    CamtrawlAcquisition.py is the image acquisition application for the
    Camtrawl image acquisition platform. The application can be used to
    collect images and video from Flir machine vision cameras compatible
    with the Flir Spinnaker SDK. Experimental support for V4L2 cameras
    is in the works.

    If available, the application will connect to the Camtrawl power and
    control interface (aka the Camtrawl Controller) to log its sensor
    streams and trigger the cameras and strobes. If the controller is not
    available, the application will trigger cameras using software triggering.
    """

    def __init__(self, **kwargs):
        # call the parent class's init method, passing our args along
        super().__init__(**kwargs)

        # Define additional deafult properties
        self.controller = None
        self.controllerStarting = False
        self.HWTriggerHDR = {}
        self.controller_port = {}

        # Add default config values for the controller and sensor integration
        # that CamtrawlAcquisition adds to AcquisitionBase.

        self.configuration['controller'] = {}
        self.configuration['sensors'] = {}

        self.configuration['controller']['use_controller'] = False
        self.configuration['controller']['serial_port'] = 'COM3'
        self.configuration['controller']['baud_rate'] = 57600
        self.configuration['controller']['strobe_pre_fire'] = 150

        self.configuration['sensors']['default_type'] = 'synchronous'
        self.configuration['sensors']['synchronous'] = ['$OHPR']
        self.configuration['sensors']['asynchronous'] = ['$CTCS', '$SBCS', '$IMUC', '$CTSV']
        self.configuration['sensors']['synchronous_timeout'] = 5


    def AcquisitionSetup(self):
        '''AcquisitionSetup reads the configuration files, creates the log file,
        opens up the metadata database, and sets up the cameras.
        '''
        # call the base class's AcquisitionSetup to perform
        super().AcquisitionSetup()

        # If isAcquiring is true we know our cameras are ready to log data. If we're
        # supposed to use the Camtrawl Controller, we start it here.
        if self.isAcquiring:
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


    def ConfigureCameras(self):
        """
        ConfigureCameras runs through the cameras visible to Spinnaker and configures
        cameras according to the settings in the camera section of the configuration file.
        """

        # call the base class's ConfigureCameras method
        ok = super().ConfigureCameras()

        #  initialize some properties specific to CamtrawlAcquisition
        self.controller_port = {}

        # now we work through our configured cameras and set up some Camtrawl
        # controller specific bits.
        for sc in self.cameras:

            # get the configuration for this camera.
            _, config = self.GetCameraConfiguration(sc.camera_name)

            # The Camtrawl controller has two camera trigger ports, 0 and 1.
            # You must specify the controller port each camera is connected to
            # to ensure they are triggered correctly. This dict allows us
            # to map the individual camera objects to their controller ports.
            self.controller_port[sc] = config['controller_trigger_port']

            # Here we connect the camera's triggerReady signal to this class's
            # HWTriggerReady slot. This signal informs the app when a
            # camera is ready to trigger and when all cameras are ready, the
            # app tells the controller to hardware trigger the cameras.
            if self.configuration['controller']['use_controller']:
                if sc in self.hw_triggered_cameras:
                    sc.triggerReady.connect(self.HWTriggerReady)

        return ok


    def AcqisitionTeardown(self):
        """
        AcqisitionTeardown is called when the application is shutting down.
        The cameras will have already been told to stop acquiring
        """
        # call the base class's AcqisitionTeardown method
        super().AcqisitionTeardown()

        #  clean up some CamtrawlAcquisition specific objects
        del self.readyToTrigger
        del self.HWTriggerHDR
        del self.controller_port


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

        #  if any cameras are hardware triggered we have to track some other info
        if self.hwTriggered:
            #  reset the image received state for hardware triggered cameras
            self.ctcTriggerChannel = [False] * len(self.hw_triggered_cameras)
            self.maxExposure = 0
            for c in self.hw_triggered_cameras:
                self.readyToTrigger[c] = False
                self.HWTriggerHDR[c] = False

        # call the base class's TriggerCameras method
        super().TriggerCameras()


    @QtCore.pyqtSlot(object, list, bool)
    def HWTriggerReady(self, cam, exposure_us, is_HDR):
        '''
        The HWTriggerReady slot is called by each hardware triggered camera when it
        is ready to be triggered. We track the responses and when all cameras are
        ready, we call the CamtrawlController's trigger method which will trigger
        the strobes and the cameras.
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


def exitHandler(a,b=None):
    '''
    exitHandler is called when CTRL-c is pressed on Windows
    '''
    global ctrlc_pressed

    if not ctrlc_pressed:
        #  make sure we only act on the first ctrl-c press
        ctrlc_pressed = True
        print("CTRL-C detected. Shutting down...")
        acquisition.StopAcquisition(exit_app=True)

    return True


def signal_handler(*args):
    '''
    signal_handler is called when ctrl-c is pressed when the python console
    has focus. On Linux this is also called when the terminal window is closed
    or when the Python process gets the SIGTERM signal.
    '''
    global ctrlc_pressed

    if not ctrlc_pressed:
        #  make sure we only act on the first ctrl-c press
        ctrlc_pressed = True
        print("CTRL-C or SIGTERM/SIGHUP detected. Shutting down...")
        acquisition.StopAcquisition(exit_app=True)

    return True


if __name__ == "__main__":
    import sys
    import argparse

    #  create a state variable to track if the user typed ctrl-c to exit
    ctrlc_pressed = False

    #  Set up the handlers to trap ctrl-c
    if sys.platform == "win32":
        #  On Windows, we use win32api.SetConsoleCtrlHandler to catch ctrl-c
        import win32api
        win32api.SetConsoleCtrlHandler(exitHandler, True)
    else:
        #  On linux we can use signal to get not only ctrl-c, but
        #  termination and hangup signals also.
        import signal
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
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



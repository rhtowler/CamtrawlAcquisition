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
.. module:: CamtrawlAcquisition.SimpleAcquisition

    :synopsis: SimpleAcquisition is a simplified version of the
               CamtrawlAcquisition application that provides basic
               image acquisition functionality without the requirement
               to use the Camtrawl power and control interface.

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


class SimpleAcquisition(AcquisitionBase):
    '''
    SimpleAcquisition is a thin wrapper around AcquisitionBase that provides
    all of the functionality of AcquisitionBase
    '''

    def __init__(self, **kwargs):
        # call the parent class's init method, passing our args along
        super().__init__(**kwargs)


    def AcquisitionSetup(self):
        # call the base class's AcquisitionSetup
        super().AcquisitionSetup()

        # If isAcquiring is true we know our cameras are ready to log data. Since
        # SimpleAcquisition isn't using the Camtrawl Controller we just start
        # triggering once the system is ready. We delay this first trigger for
        # 1/2 a second to give the cameras time to finish getting ready.
        if self.isAcquiring:
            self.triggerTimer.start(500)


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
    import signal

    #  set up handlers to trap ctrl-c and (on linux) terminal close events
    #  This allows the user to stop the application with ctrl-c and at
    #  least on linux cleanly shut down when the terminal window is closed.
    #  (Windows does not expose those signals)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    if os.name != 'nt':
        signal.signal(signal.SIGHUP, signal_handler)

    #  set the default application config file path
    config_file = "./SimpleAcquisition.yml"
    profiles_file = './VideoProfiles.yml'

    #  parse the command line arguments
    parser = argparse.ArgumentParser(description='SimpleAcquisition')
    parser.add_argument("-c", "--config_file", help="Specify the path to the yml configuration file.")
    parser.add_argument("-p", "--profiles_file", help="Specify the path to the yml video profiles definition file.")
    args = parser.parse_args()

    if (args.config_file):
        config_file = os.path.normpath(str(args.config_file))
    if (args.profiles_file):
        profiles_file = os.path.normpath(str(args.profiles_file))

    #  create an instance of QCoreApplication and and instance of the acquisition application
    app = QtCore.QCoreApplication(sys.argv)
    acquisition = SimpleAcquisition(config_file=config_file, profiles_file=profiles_file,
            parent=app)

    #  and start the event loop
    sys.exit(app.exec_())

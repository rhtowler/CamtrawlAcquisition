# CamtrawlAcquisition

CamtrawlAcquisition is the main data acquisition application for the Camtrawl underwater stereo camera system.

The Camtrawl system is a self-contained underwater stereo camera system mounted near the codend of a research trawl which provides information on species and size composition along the trawl path to inform scientists conducting fisheries research and stock assessment surveys. CamtrawlAcquisition is designed to collect the image and sensor data that is subsequently processed to provide the species and size data.

The system is comprised of a single board computer, two Flir machine vision cameras, LED strobes, and some custom power and control electronics we call the Camtrawl Controller. While on deck, the camera system is sleeping. When it is deployed, the SBC is turned on and the application starts and data is collected. As the system nears the surface at the end of the deployment the control electronics signal the acquisition application to exit and shut down the PC. A single run of the application is considered a collection event and all data for that event is contained within a single directory that is named after the time the application was started.

This package also contains a simplified acquisition application called (unimaginatively) SimpleAcquisition.py. This application does not require the Camtrawl Controller and can be used to collect data from one or more compatible cameras that are software triggered.

These applications are console based and use YAML files for configuration.

### Limitations

Currently these applications only work with Teledyne FLIR (formerly Point Grey Research) machine vision cameras compatible with their [Spinnaker SDK]([Spinnaker SDK | Teledyne FLIR](https://www.flir.com/products/spinnaker-sdk/)).  We are working to add support for V4L2 compatible cameras which includes Raspberry Pi CSI and many USB based cameras.

### Python Dependencies

Python 3+
PyQt5
[Numpy](https://pypi.org/project/numpy/)
PySpin (from [Flir Spinnaker SDK](https://www.flir.com/support-center/iis/machine-vision/downloads/spinnaker-sdk-and-firmware-download/))
[PyAv](https://pypi.org/project/av/)
[OpenCV](https://pypi.org/project/opencv-python/)
[protobuf](https://pypi.org/project/protobuf/)


from PyQt5.QtCore import pyqtSignal, QObject, QThread, pyqtSlot
import SerialDevice


class SerialMonitor(QObject):
    """A class for acquiring data from multiple serial port devices.

    SerialMonitor watches a collection of serial ports and emits a signal when
    data is received by any of the monitored ports. SerialMonitor does this by
    spawning a thread for each port that is monitored that periodically
    polls the serial port, buffering data until a complete line is received. (A
    complete line is defined as a line terminated by LF, CR+LF, or CR.) The line
    can optionally be parsed and the resulting output is passed from the
    monitoring thread to SerialMonitor via Qt's signal/Slot mechanism which
    then re-emits these signals which are then handled by the application's
    serial event handling method. SerialMonitor handles both sending and receiving
    of data and thus can be used for polled sensors and for general serial I/O.

    **Public Methods**

    **addDevice** -- add a serial device to the list of devices that
    SerialMonitor watches.

    **removeDevice** -- Remove a device from the list of devices that
    SerialMonitor watches. If the device is currently being monitored,
    the acquisition thread is stopped and the serial port is closed.

    **startMonitoring** -- Start monitoring all (or optionally some) of the
    devices that have been registered by calls to addDevice. Calling
    startMonitoring will open the serial ports and start the acquisition
    threads for the specified devices.

    **stopMonitoring** -- Stop monitoring all (or optionally some) of the
    devices that have been registered by calls to addDevice. The acquisition
    threads are stopped and the serial ports closed for the specified devices.

    **Signals**

    **SerialDataReceived** -- This signal is emitted when a complete line of
    data is received by one of the monitored serial ports. If the line is
    parsed, the signal is only emitted if the parsing method returns data.

    Applications wishing to receive data from SerialMonitor must connect
    this signal to a method that accepts the following parameters:

        @rtype: String
        @returns: The device name string, as defined in the call to addDevice.
        @rtype: String
        @returns: The data received by the port identified by the device name.
        @rtype: Exception
        @returns: If there is an error parsing the data,

    """

    #  define this class's signals
    SerialControlState = pyqtSignal(str, str, bool)
    SerialControlChanged = pyqtSignal(str, dict)
    SerialDataReceived = pyqtSignal(str, object, object)
    txSerialData = pyqtSignal(str, str)
    getSerialCTL = pyqtSignal(str)
    setSerialRTS = pyqtSignal(str, bool)
    setSerialDTR = pyqtSignal(str, bool)
    stopDevice = pyqtSignal(list)


    def __init__(self, parent=None):
        """Initialize this SerialMonitor instance."""
        super(SerialMonitor, self).__init__(None)
        #QObject.__init__(self, parent)

        #  create the devices dictionary which is keyed by device name and stores the
        #  various parameters for that device. (Unlike earlier versions, this dict does
        #  not store the reference to the serialDevice object.
        self.devices = dict()

        #  create the threads dictionary which is keyed by the QThread object and
        #  stores a reference to the serialDevice object.
        self.threads = dict()


    def __del__(self):
        """Clean up any active QserialThread objects before deleting the SerialMonitor instance."""

        #  call stopMonitoring
        self.stopMonitoring()


    def addDevice(self, deviceName, port, baud, parseType, parseExp, parseIndex, cmdPrompt='', \
                  byteSize=8, parity='N', stopBits=1, flowControl='NONE', pollRate=500,
                  txRate=500, initialState = (True, True)):
        """Add a serial device to the list of devices that SerialMonitor watches.

        *deviceName* is a string that serves as a unique identfier for the serial port.
        This name will be included in the emitted signals to associate data with a device.

        *port* is a string containing the platform specific serial port identifier. For
        example, on windows systems it would be ``'COM1'`` or ``'COM12'``. On Linux systems it
        would be ``'/dev/ttyS0'`` or similar. You can also specify the IP address and port
        for RFC 2217 Network ports by setting the port string to 'rfc2217://<host>:<port>'

        *baud* is the serial port baud rate such as 9600 or 115200 etc.

        *parseType* specifies how the incoming data is parsed before the data is emitted
        via the *SerialDataReceived* signal. Valid values are:

            ``None`` -- No parsing is performed but newline characters are stripped.

            ``Delimited`` -- The incoming line is parsed using a delimiter. The delimiter
            is specified in the *parseExp* argument and the *parseIndex* argument specifies
            which field is returned.

            ``RegEx`` -- The incoming line is parsed using a regular expression. The regex
            is passed via the *parseExp* argument and and the *parseIndex* argument specifies
            which field is returned.

            ``RFIDFDXB`` -- This is a fixed length parser that assumes the data conforms to
            the FDX-B RFID tag specification. The *parseIndex* argument specifies the length
            in bytes of the datagram which for now should be specified as 8.

            ``HexEncode`` -- This is a fixed length parser that assumes the data is hex encoded.
            The *parseIndex* argument specifies the length in bytes of the datagram.

            ``FixedLen`` -- This is a generic fixed length parser that simply returns data in
            chunks of bytes the size of which is specified in the *parseIndex* argument.

            ``RAMSES`` -- This is a specialized fixed length parser for the RAMSES-ACC series of
            hyperspectral radiometers. Set *parseExp* and *parseIndex* to NONE.

        *parseExp* is a string defining either the parsing delimiter if *parseType* is 1
        or the regular expression if *parseType* is set to 2. If `parseType` is 0, specify
        an empty string ``''``.

        *parseIndex* is a number that is the index into the list returned by the parsing
        method of the data element of interest. This parameter must be specified even if
        only 1 element is returned from the parsing method (in which case *parseIndex*
        would be set to ``0``. For the fixed length parsers this argument specified the length
        of the message that should be returned.

        *cmdPrompt* (optional) is a string that specifies the command prompt for instruments
        that require user interaction. Text based UI's that require user interaction may
        output a command prompt which lacks a newline character. Setting *cmdPrompt* to
        the text of this command prompt will result in the command prompt line being
        handled like a regular line where the text of the command prompt is emitted via
        the *SerialDataReceived* signal. This allows the method handling the
        *SerialDataReceived* signal to "respond" to the command prompt.

        *byteSize* (optional) a number specifying the number of data bits. Possible values
        are ``5``, ``6``, ``7``, and ``8``. Default is ``8``.

        *parity* (optional) a string specifying the serial port parity checking method.
        Possible values are ``N`` for None, ``E`` for Even, ``O`` for Odd, ``M`` for Mark,
        and ``S`` for Space. Default is ``N``.

        *stopBits* (optional) a number specifying the number of stop bits. Possible values
        are ``1``, ``1.5``, and ``2``. Default is ``1``.

        *flowControl* (optional) a string specifying the flow control method. Possible
        values are ``RTSCTS`` for RTS/CTS hardware flow control, ``DSRDTR`` for DSR/DTR
        hardware flow control, ``SOFTWARE`` for XON/XOFF software flow control and
        ``NONE`` for no flow control. Default is ``NONE``.

        *pollRate* (optional) a number specifying the rate (in Hz) that the serial port
        is polled. During polling the input buffer is checked for data and if data is present
        the buffer is read from. Valid values are in the range of 1-1000 Hz.
        The default value is 1000 Hz.

        *txRate* (optional) a number specifying the rate (in Hz) that the tx buffer
        is polled. During polling the output buffer is checked for data and if data is present
        one "line" of data is transmitted. This value can be set to help throttle transmits
        to devices that cannot keep up with the flow of data. Valid values are in the range
        of 1-1000 Hz. The default value is 1000 Hz.

        *initialState* (optional) a 2-tuple of booleans containing the initial state of the
        control lines RTS and DTR (in that order) for the serial port when added to the
        monitor
        """

        if deviceName in self.devices:
            #  device name is already in use - issue error
            raise SerialError('Device name ' + deviceName + ' is already in use. Specify a unique name.')

        #  store the parameters for this device - we don't actually create the device here. We but
        #  create the SerialMonitorThread object when the device is started.
        self.devices[deviceName] = {'deviceName':deviceName,
                                    'port':port,
                                    'baud':baud,
                                    'parseType':parseType,
                                    'parseExp':parseExp,
                                    'parseIndex':parseIndex,
                                    'cmdPrompt':cmdPrompt,
                                    'byteSize':byteSize,
                                    'parity':parity,
                                    'stopBits':stopBits,
                                    'flowControl':flowControl,
                                    'pollRate':pollRate,
                                    'txRate':txRate,
                                    'initialState':initialState,
                                    'thread':None}


    def startMonitoring(self, devices=None):
        """
          Start monitoring creates an instance of SerialMonitorThread for each device
          specified, moves the object to a new thread, and starts the thread. The
          SerialMonitorThread objects then open their serial port and start polling
          As data is received from the individual ports it is sent via the
          ``SerialDataReceived`` signal.

          You can start specific devices by setting the `devices` keyword to a list
          of device(s) you want to start. If you do not specify any devices, all
          devices will be started.
        """


        if devices == None:
            #  no devices specified - get a list of all devices
            devices = self.devices.keys()
        elif (type(devices) is str):
            #  device was specified as a string, put it in a list
            devices = [devices]

        #  iterate through the provided devices, starting each one
        for device in devices:

            #  create the serialDevice object
            serialDevice = SerialDevice.SerialDevice(self.devices[device])

            #  connect us to the SerialMonitorThread's signals
            serialDevice.SerialDataReceived.connect(self.dataReceived)
            serialDevice.SerialControlChanged.connect(self.controlDataChanged)
            serialDevice.DCEControlState.connect(self.controlDataState)
            serialDevice.SerialError.connect(self.serialError)
            self.stopDevice.connect(serialDevice.stopPolling)

            #  connect our signals to the SerialMonitorThread
            self.txSerialData.connect(serialDevice.write)
            self.getSerialCTL.connect(serialDevice.getControlLines)
            self.setSerialRTS.connect(serialDevice.setRTS)
            self.setSerialDTR.connect(serialDevice.setDTR)

            #  create a thread to run the monitor in
            thread = QThread(self)

            #  move the monitor to it
            serialDevice.moveToThread(thread)

            #  connect thread specific signals and slots - this facilitates starting,
            #  stopping, and deletion of the threads.
            thread.started.connect(serialDevice.startPolling)
            serialDevice.SerialPortClosed.connect(thread.quit)
            thread.finished.connect(self.removeDevice)
            thread.finished.connect(thread.deleteLater)

            #  store references to our new objects
            self.threads[thread] = serialDevice
            self.devices[device]['thread'] = thread

            #  and finally, start the thread - this will also start polling
            thread.start()


    def stopMonitoring(self, devices=None):
        """
          Stop monitoring emits the ``stopDevice`` signal which informs the
          SerialDevice thread to stop polling, flush and close the serial port, and
          terminate the thread.

          You can stop specific devices by setting the `devices` keyword to a list
          of device(s) you want to stop. If you do not specify any devices, all
          devices will be stopped.
        """

        if devices == None:
            #  no devices specified - get a list of all devices
            devices = list(self.devices.keys())
        elif (type(devices) is str):
            #  device was specified as a string, put it in a list
            devices = [devices]

        self.stopDevice.emit(devices)


    def whosMonitoring(self):
        """Returns a list of currently running serial acquisition threads"""
        runningDevices = []
        for device in self.devices.iter():
            #  assume if thread is populated, then the thread is running
            if device['thread']:
                runningDevices.append(device[0])

        return runningDevices


    def setDTR(self, deviceName, state):
        """Set the DTR line on the specified serial port

        """
        if deviceName in self.devices:
            self.setSerialDTR.emit(deviceName, state)


    def setRTS(self, deviceName, state):
        """Set the RTS line on the specified serial port

        """
        if deviceName in self.devices:
            self.setSerialRTS.emit(deviceName, state)


    def txData(self, deviceName, data):
        """Transmit data to the specified device

        `deviceName` must be set to the name of a configured
        """

        #  send the txSerialData signal to the monitoring threads
        if deviceName in self.devices:
            self.txSerialData.emit(deviceName, data)


    def getControlLines(self, deviceName):
        """
            Request the status of the DCE control lines. The data is returned as a
            list of booleans ordered as [CTS, DSR, RI, CD].
        """
        self.getSerialCTL.emit(deviceName)


    @pyqtSlot()
    def dataReceived(self, deviceName, data, err):
        # consolidates the RX data signals from the individual monitoring threads and re-emit
        self.SerialDataReceived.emit(deviceName, data, err)


    @pyqtSlot(str, list)
    def controlDataState(self, deviceName, state_list):
        # consolidates the signals from the individual monitoring threads and re-emit
        state = {'CTS':state_list[0],
                 'DSR':state_list[1],
                 'RI':state_list[2],
                 'CD':state_list[3]}
        self.SerialControlChanged.emit(deviceName, state)


    @pyqtSlot()
    def controlDataChanged(self, deviceName, line, state):
        # consolidates the signals from the individual monitoring threads and re-emit
        self.SerialControlState.emit(deviceName, state)


    @pyqtSlot(str, object)
    def serialError(self, deviceName, errorObj):
        # consolidates the error signals from the individual monitoring threads and raises
        #  them in the main application thread.
        raise errorObj


    def removeDevice(self):
        """
          removeDevice is called when a SerialMonitorThread thread instance finishes
          running and emits the "finished" signal. This method cleans up references
          to that thread and SerialMonitorThread object.

          This method should not be called directly.
        """

        #  get a reference to the thread that is shutting down
        thread = QObject.sender(self)

        #  get the name of the device that is shutting down
        deviceName = self.threads[thread].deviceName

        #print('Stopping ' + deviceName, '   Thread:', thread)

        #  update the thread
        self.devices[deviceName]['thread'] = None

        #  delete the reference to the thread
        del self.threads[thread]



#
#  SerialMonitor Exception class
#
class SerialError(Exception):
    def __init__(self, msg, parent=None):
        self.errText = msg
        self.parent = parent

    def __str__(self):
        return repr(self.errText)


class SerialPortError(Exception):
    def __init__(self, devices, parent=None):
        self.devices = devices
        self.devNames = devices.keys()
        if (len(devices) == 1):
            self.errText = 'Error opening device ' + str(self.devNames[0])
        else:
            self.errText = 'Error opening devices ' + ','.join(self.devNames)

    def __str__(self):
        return repr(self.errText)

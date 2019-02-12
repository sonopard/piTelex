#!/usr/bin/python
"""
Telex Device - Serial Communication over CH340-Chip (not FTDI, not Prolific, not CP213x)
"""
__author__      = "Jochen Krapf"
__email__       = "jk@nerd2nerd.org"
__copyright__   = "Copyright 2018, JK"
__license__     = "GPL3"
__version__     = "0.0.1"

import serial
import time

import txCode
import txBase

#######

class TelexCH340TTY(txBase.TelexBase):
    def __init__(self, mode:str, **params):

        super().__init__()

        self.id = '~'
        self.params = params

        portname = params.get('portname', '/dev/ttyUSB0')
        baudrate = params.get('baudrate', 50)
        bytesize = params.get('bytesize', 5)
        stopbits = params.get('stopbits', serial.STOPBITS_ONE_POINT_FIVE)
        uscoding = params.get('uscoding', False)
        loopback = params.get('loopback', None)
        self._local_echo = params.get('loc_echo', False)

        self._rx_buffer = []
        self._tx_buffer = []
        self._counter_LTRS = 0
        self._counter_FIGS = 0
        self._counter_dial = 0
        self._time_last_dial = 0
        self._cts_stable = True   # rxd=Low 
        self._cts_counter = 0
        self._time_squelch = 0
        self._is_enabled = False
        self._is_online = False

        self._set_mode(mode)
        if loopback is not None:
            self._loopback = loopback

        # init serial
        self._tty = serial.Serial(portname, write_timeout=0)

        if baudrate not in self._tty.BAUDRATES:
            raise Exception('Baudrate not supported')
        if bytesize not in self._tty.BYTESIZES:
            raise Exception('Databits not supported')
        if stopbits not in self._tty.STOPBITS:
            raise Exception('Stopbits not supported')

        self._tty.baudrate = baudrate
        self._tty.bytesize = bytesize
        self._tty.stopbits = stopbits
        self._baudrate = baudrate

        # init codec
        #character_duration = (bytesize + 1.0 + stopbits) / baudrate
        character_duration = (bytesize + 3.0 ) / baudrate   # CH340 sends always with 2 stop bits
        self._mc = txCode.BaudotMurrayCode(self._loopback, us_coding=uscoding, character_duration=character_duration)

        self._set_enable(False)
        self._set_online(False)

    # -----

    def _set_mode(self, mode:str):
        self._loopback = False
        self._use_pulse_dial = False
        self._use_squelch = False
        self._use_cts = False
        self._inverse_cts = False
        #self._use_dtr = False
        self._inverse_dtr = False
        self._inverse_rts = False
        self._use_dedicated_line = True

        mode = mode.upper()

        if mode.find('TW39') >= 0:
            self._loopback = True
            self._use_cts = True
            self._use_pulse_dial = True
            self._use_squelch = True
            self._use_dedicated_line = False

        if mode.find('TWM') >= 0:
            self._loopback = True
            self._use_cts = True
            self._use_pulse_dial = True
            self._use_squelch = True
            self._use_dedicated_line = False

        if mode.find('V.10') >= 0 or mode.find('V10') >= 0:
            self._use_cts = True
            self._inverse_cts = True
            #self._inverse_dtr = True

    # -----

    def __del__(self):
        #print('__del__ in TelexSerial')
        self._tty.close()
        super().__del__()
    
    # =====

    def read(self) -> str:
        if self._tty.in_waiting:
            a = ''

            bb = self._tty.read(1)
            #self._tty.write(bb)

            if bb and (not self._use_squelch or time.time() >= self._time_squelch):
                if self._is_enabled or self._use_dedicated_line:
                    a = self._mc.decodeBM2A(bb)

                    if a:
                        self._check_special_sequences(a)

                elif self._use_pulse_dial:
                    b = bb[0]

                    if b == 0:   # break or idle mode
                        pass
                    elif (b & 0x13) == 0x10:   # valid dial pulse - min 3 bits = 40ms, max 5 bits = 66ms
                        self._counter_dial += 1
                        self._time_last_dial = time.time()
                
                self._cts_counter = 0

                if a:
                    self._rx_buffer.append(a)
                    if self._local_echo:
                        self._tx_buffer.append(a)

        if self._rx_buffer:
            ret = self._rx_buffer.pop(0)
            return ret

    # -----

    def write(self, a:str, source:str):
        if len(a) != 1:
            self._check_commands(a)
            return
            
        if a == '#':
            a = '@'   # ask teletype for hardware ID

        if a:
            self._tx_buffer.append(a)

    # =====

    def idle20Hz(self):
        time_act = time.time()

        if self._use_pulse_dial and self._counter_dial and (time_act - self._time_last_dial) > 0.5:
            if self._counter_dial >= 10:
                self._counter_dial = 0
            a = str(self._counter_dial)
            self._rx_buffer.append(a)
            self._time_last_dial = time_act
            self._counter_dial = 0

        if self._use_cts:
            cts = not self._tty.cts != self._inverse_cts   # logical xor
            if cts != self._cts_stable:
                self._cts_counter += 1
                if self._cts_counter == 20:
                    self._cts_stable = cts
                    print(cts)   # debug
                    if not cts:   # rxd=Low
                        self._rx_buffer.append('\x1bST')
                        pass
                    elif not self._is_enabled:   # rxd=High
                        self._rx_buffer.append('\x1bAT')
                        pass
                    pass
            else:
                self._cts_counter = 0

    # -----

    def idle(self):
        if not self._use_squelch or time.time() >= self._time_squelch:
            if self._tx_buffer:
                a = self._tx_buffer.pop(0)
                bb = self._mc.encodeA2BM(a)
                self._tty.write(bb)

    # -----

    def _set_online(self, online:bool):
        self._is_online = online
        self._tty.rts = online != self._inverse_rts    # RTS

    # -----

    def _set_enable(self, enable:bool):
        self._is_enabled = enable
        self._tty.dtr = enable != self._inverse_dtr    # DTR -> True=Low=motor_on
        self._mc.reset()
        if self._use_squelch:
            self._set_time_squelch(0.5)
        #self._tty.send_break(1.0)

    # -----

    def _set_pulse_dial(self, enable:bool):
        if not self._use_pulse_dial:
            return
        
        if enable:
            self._tty.baudrate = 75
        else:
            self._tty.baudrate = self._baudrate

    # -----

    def _check_special_sequences(self, a:str):
        if not self._use_cts:
            if a == '[':
                self._counter_LTRS += 1
                if self._counter_LTRS == 5:
                    self._rx_buffer.append('\x1bST')
            else:
                self._counter_LTRS = 0

            if a == ']':
                self._counter_FIGS += 1
                if self._counter_FIGS == 5:
                    self._rx_buffer.append('\x1bAT')
            else:
                self._counter_FIGS = 0

    # -----

    def _check_commands(self, a:str):
        enable = None

        if a == '\x1bA':
            self._set_pulse_dial(False)
            self._set_online(True)
            enable = True

        if a == '\x1bZ':
            #self._tty.    # TODO empty write buffer...
            self._set_pulse_dial(False)
            self._set_online(False)
            enable = False   #self._use_dedicated_line
            if not enable and self._use_squelch:
                self._set_time_squelch(1.5)

        if a == '\x1bWB':
            self._set_online(True)
            if self._use_pulse_dial:   # TW39
                self._set_pulse_dial(True)
                self._tty.write(b'\x01')   # send pulse with 25ms low to signal 'ready for dialing' ('Wahlbereitschaft')
                enable = False
            else:   # dedicated line, TWM, V.10
                enable = True

        if enable is not None:
            self._set_enable(enable)

    # -----

    def _set_time_squelch(self, t_diff):
        t = time.time() + t_diff
        if self._time_squelch < t:
            self._time_squelch = t

#######

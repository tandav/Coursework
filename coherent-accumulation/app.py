from pyqtgraph.Qt import QtCore, QtGui
from PyQt5.QtGui import QApplication
from scipy.fftpack import fft
from scipy.io.wavfile import write as write_wav
from scipy.io import wavfile
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib
matplotlib.style.use('classic')
import pyqtgraph as pg
import numpy as np
import time
import threading
import sys
import serial # TODO: try del
import serial.tools.list_ports
import socket
import signal
import os
import gzip
import shutil


class SerialReader(threading.Thread):
    """ Defines a thread for reading and buffering serial data.
    By default, about 5MSamples are stored in the buffer.
    Data can be retrieved from the buffer by calling get(N)"""
    def __init__(self, data_collected_signal, chunkSize=1024, chunks=5000):
        threading.Thread.__init__(self)
        # circular buffer for storing serial data until it is
        # fetched by the GUI
        self.buffer = np.zeros(chunks*chunkSize, dtype=np.uint16)
        self.chunks = chunks        # number of chunks to store in the buffer
        self.chunkSize = chunkSize  # size of a single chunk (items, not bytes)
        self.ptr = 0                # pointer to most (recently collected buffer index) + 1
        # self.port = port            # serial port handle
        self.port = self.find_device_and_return_port()           # serial port handle

        self.exitFlag = False
        self.exitMutex = threading.Lock()
        self.dataMutex = threading.Lock()
        self.values_recorded = 0
        self.data_collected_signal = data_collected_signal

        self.series_n = 10
        # self.series_n = 4
        self.matrix = np.zeros((self.series_n, self.chunkSize * 110))
        self.tone_playing = 0 # 0/1 here instead of False/True
        self.current_tone_i = 0
        self.out = np.zeros(self.matrix.shape[1])
        self.ptr = 0

        self.rate = 0
        self.count = 0
        self.series_start_t = time.time()

    def find_device_and_return_port(self):
        for i in range(61):
            ports = list(serial.tools.list_ports.comports())
            for port in ports:
                if 'Arduino' in port.description or \
                   'Устройство с последовательным интерфейсом USB' in port.description or \
                   'USB Serial Device' in port.description: 
                # if ('Устройство с последовательным интерфейсом USB') in port.description: 
                    # try / except
                    ser = serial.Serial(port.device)
                    print('device connected')
                    break
            else:
                if i == 60:
                    print('\nDevice not found. Check the connection.')
                    sys.exit()
                sys.stdout.write('\rsearching device' + '.'*i + ' ')
                sys.stdout.flush()
                time.sleep(0.05)
                continue  # executed if the loop ended normally (no break)
            break  # executed if 'continue' was skipped (break)
        return ser
   
    def run(self):
        exitMutex = self.exitMutex
        dataMutex = self.dataMutex
        buffer = self.buffer
        port = self.port


        global record_buffer, recording, values_to_record, t2, record_end_time, NFFT, gui, overlap

        while True:
            # see whether an exit was requested
            with exitMutex:
                if self.exitFlag:
                    port.close()
                    break

            # read one full chunk from the serial port
            # (chunkSize) uint16 samples == (chunkSize * 2) bytes
            data = port.read(self.chunkSize * 2) 
            # convert data to 16bit int numpy array TODO, [KINDA DONE]: convert here to -1..+1 values, instead voltage 0..3.3

            # dirty hotfix
            if data[:4] == b'\xd2\x02\x96I' or data[4:8] == b'\xd2\x02\x96I':
                timings = np.frombuffer(data, dtype=np.uint32)
                
                current_tone_i_old = self.current_tone_i
                if data[:4] == b'\xd2\x02\x96I':
                    self.tone_playing   = timings[1]
                    self.current_tone_i = timings[2] % self.series_n
                elif data[4:8] == b'\xd2\x02\x96I':
                    self.tone_playing   = timings[2]
                    self.current_tone_i = timings[3] % self.series_n
                if self.current_tone_i != current_tone_i_old:
                    self.ptr = 0
            else:
                if self.tone_playing:
                    data = np.frombuffer(data, dtype=np.uint16)
                    with dataMutex:
                        if self.current_tone_i == 0 and self.ptr == 0: # end of series (start of new series), need to update plot
                            self.out = np.mean(self.matrix, axis=0)
                            self.data_collected_signal.emit() # try pass array via signal?
                            self.matrix = np.zeros((self.series_n, self.out.shape[0]))
                            self.rate = self.count / (time.time() - self.series_start_t)
                            self.series_start_t = time.time()
                            self.count = 0
                        self.matrix[self.current_tone_i, self.ptr : self.ptr + self.chunkSize] = data
                        self.ptr += self.chunkSize
                # collect samples for computing rate 
                #   - even when tone_playing == False
                #       - because the whole series dt is measured (with silences between tones))
                self.count += self.chunkSize



    def get(self):
        """ Return a tuple (time_values, voltage_values, rate)
          - voltage_values will contain the *num* most recently-collected samples
            as a 32bit float array.
          - time_values assumes samples are collected at 1MS/s
          - rate is the running average sample rate.
        """


        # Convert array to float and rescale to voltage.
        # Assume 3.3V / 12bits
        # (we need calibration data to do a better job on this)
        # data = data.astype(np.float32) * (3.3 / 2**12) * 2 / 3.3 - 1

        with self.dataMutex:
            out = self.out
            rate = self.rate
        # print(out.dtype)
        out = out * (3.3 / 2**12) * 2 / 3.3 - 1
        return out, rate

    def exit(self):
        """ Instruct the serial thread to exit."""
        with self.exitMutex:
            self.exitFlag = True



class AppGUI(QtGui.QWidget):
    data_collected = QtCore.pyqtSignal()

    def __init__(self):
        super(AppGUI, self).__init__()
        
        self.init_ui()
        self.qt_connections()

    def init_ui(self):
        global record_name, NFFT, chunkSize, overlap
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')

        self.setWindowTitle('Signal from stethoscope')
        self.layout = QtGui.QVBoxLayout()




        self.fft_widget = pg.PlotWidget(title='FFT')
        self.fft_widget.showGrid(x=True, y=True, alpha=0.1)
        self.fft_widget.setLogMode(x=True, y=False)
        # self.fft_widget.setLogMode(x=False, y=False)
        # self.fft_widget.setYRange(0, 0.1) # w\o np.log(a)
        self.fft_widget.setYRange(-60, 80) # w/ np.log(a)
        self.fft_curve = self.fft_widget.plot(pen='r')

        self.layout.addWidget(self.fft_widget)


        self.setLayout(self.layout)
        self.setGeometry(10, 10, 800, 600)
        self.show()

    def qt_connections(self):
        self.data_collected.connect(self.updateplot)

    @QtCore.pyqtSlot()
    def updateplot(self):
        y, rate = ser_reader_thread.get()
        n = len(y)

        a = np.fft.rfft(y * np.hanning(n))

        # # в 2 строчки быстрее чем в одну! я замерял!
        a = np.abs(a) # magnitude
        a = 20 * np.log10(a) # часто ошибка - сделать try, else

        
        if rate > 0:
            f = np.fft.rfftfreq(n, d = 1. / rate)
            self.fft_widget.getPlotItem().setTitle(f'Sample Rate: {rate/1000:0.2f} kHz')
            self.fft_curve.setData(f, a)
    def closeEvent(self, event):
        global ser_reader_thread
        ser_reader_thread.exit()



def main():
    # globals
    global gui, ser_reader_thread, chunkSize, big_dt
    chunkSize = 256
    chunks    = 2000
    big_dt    = 0

    # init gui
    app = QtGui.QApplication(sys.argv)
    gui = AppGUI() # create class instance

    # init and run serial arduino reader
    ser_reader_thread = SerialReader(data_collected_signal=gui.data_collected, 
                                     chunkSize=chunkSize,
                                     chunks=chunks)
    ser_reader_thread.start()

    # app exit
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    sys.exit(app.exec())

if __name__ == '__main__':
    main()

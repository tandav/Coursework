from pyqtgraph.Qt import QtCore, QtGui
from PyQt5.QtGui import QApplication
import pyqtgraph as pg
import numpy as np
import sys
import signal
from time import time, sleep


class LungsModel():
    l_default = 0.1
    h_default = 1
    f_default = 440

    def __init__(self, L=l_default, H=h_default, F=f_default):
        self.r = np.load('../3d_numpy_array_reduced-58-64-64.npy')
        self.ro  = 1e-5 + 1.24e-3 * self.r - 2.83e-7 * self.r * self.r + 2.79e-11 * self.r * self.r * self.r
        self.c = (self.ro + 0.112) * 1.38e-6

        self.t = 0
        self.l = L # dt, time step
        self.h = H # dx = dy = dz = 1mm
        self.K = self.l / self.h * self.c
        self.K2 = self.K**2
        self.K_2_by_3 = self.K**2 / 3


        # initial conditions
        self.P_pp = np.zeros_like(self.ro) # previous previous t - 2
        self.P_p  = np.zeros_like(self.ro) # previous          t - 1
        self.P    = np.zeros_like(self.ro) # current           t

        N = self.P.shape[1]
        self.A, self.B, self.C = 2, N//2, N//2 # sound source location
        self.oA, self.oB, self.oC = 6, N//2, N//2 # sound source location

        self.f = F
        print(f'init model l={self.l} h={self.h} f={self.f}')

    def update_P(self):
        '''
        mb work with flat and then reshape in return
        norm by now, mb add some more optimisations in future, also cuda
        '''

        S = self.P_p.shape[0]
        N = self.P_p.shape[1]

        self.P[2:-2, 2:-2, 2:-2] = 2 * self.P_p[2:-2, 2:-2, 2:-2] - self.P_pp[2:-2, 2:-2, 2:-2]

        Z = np.zeros_like(self.P_p)
        Z[2:-2, 2:-2, 2:-2] = 22.5 * self.P_p[2:-2, 2:-2, 2:-2]
        
        cell_indeces_flat = np.arange(S * N * N).reshape(S, N, N)[2:-2, 2:-2, 2:-2].flatten().reshape(-1, 1) # vertical vector

        s1_indexes_flat = cell_indeces_flat + np.array([-1, 1, -N, N, -N**2, N**2])      # i±1 j±1 k±1 
        s2_indexes_flat = cell_indeces_flat + np.array([-1, 1, -N, N, -N**2, N**2]) * 2  # i±2 j±2 k±2 
        s1_values = self.P_p.flatten()[s1_indexes_flat] # each row contains 6 neighbors of cell 
        s2_values = self.P_p.flatten()[s2_indexes_flat] # each row contains 6 neighbors of cell 
        s1 = np.sum(s1_values, axis=1) # sum by axis=1 is faster for default order
        s2 = np.sum(s2_values, axis=1)

        Z[2:-2, 2:-2, 2:-2] -=   4 * s1.reshape(S-4, N-4, N-4)
        Z[2:-2, 2:-2, 2:-2] += 1/4 * s2.reshape(S-4, N-4, N-4)

        m1 = np.array([1, -1, -1/8, -1/8])
        m2 = np.array([1, -1])

        s3_V_indexes = cell_indeces_flat + np.array([N**2, -N**2, 2*N**2, -2*N**2])
        s3_V_values = self.P_p.flatten()[s3_V_indexes] * m1 # po idee mozhno za skobki kak to vinesti m1 i m2
        s3_V_sum = np.sum(s3_V_values, axis=1)
        s3_N_indexes = cell_indeces_flat + np.array([N**2, -N**2])
        s3_N_values = self.ro.flatten()[s3_N_indexes] * m2
        s3_N_sum = np.sum(s3_N_values, axis=1)
        s3 = (s3_V_sum * s3_N_sum).reshape(S-4, N-4, N-4)
        
        s4_V_indexes = cell_indeces_flat + np.array([N, -N, 2*N, -2*N])
        s4_V_values = self.P_p.flatten()[s4_V_indexes] * m1
        s4_V_sum = np.sum(s4_V_values, axis=1)
        s4_N_indexes = cell_indeces_flat + np.array([N, -N])
        s4_N_values = self.ro.flatten()[s4_N_indexes] * m2
        s4_N_sum = np.sum(s4_N_values, axis=1)
        s4 = (s4_V_sum * s4_N_sum).reshape(S-4, N-4, N-4)

        s5_V_indexes = cell_indeces_flat + np.array([1, -1, 2, -2])
        s5_V_values = self.P_p.flatten()[s5_V_indexes] * m1
        s5_V_sum = np.sum(s5_V_values, axis=1)
        s5_N_indexes = cell_indeces_flat + np.array([1, -1])
        s5_N_values = self.ro.flatten()[s5_N_indexes] * m2
        s5_N_sum = np.sum(s5_N_values, axis=1)
        s5 = (s5_V_sum * s5_N_sum).reshape(S-4, N-4, N-4)

        Z[2:-2, 2:-2, 2:-2] += (s3 + s4 + s5) * self.ro[2:-2, 2:-2, 2:-2]
        self.P[2:-2, 2:-2, 2:-2] -= Z[2:-2, 2:-2, 2:-2] * self.K_2_by_3[2:-2, 2:-2, 2:-2]
        self.P[self.ro < 0.1] = 0
      
    def step(self):
        self.P_old = self.P
        self.update_P()
        self.P[self.A, self.B, self.C] = np.sin(2 * np.pi * self.f * self.t)
        self.P_pp  = self.P_p
        self.P_p   = self.P_old
        self.t += self.l


class AppGUI(QtGui.QWidget):
    steps_state = QtCore.pyqtSignal([int])

    def __init__(self):
        # super(AppGUI, self).__init__()
        super().__init__()
        
        self.model = LungsModel()

        self.data = self.model.P
        # self.current_slice = self.data.shape[0] // 2
        self.current_slice = self.model.A
        # self.signal_window = 64
        # self.source_signal = np.zeros(self.signal_window)
        # self.observ_signal = np.zeros(self.signal_window)
        # self.oA = 10
        # self.oB = 
        # self.oB


        self.init_ui()
        self.qt_connections()

    def init_ui(self):
        pg.setConfigOption('background', 'w')

        self.setGeometry(50, 50, 700, 700)
        self.setWindowTitle('Lungs Model')
        self.l_label = QtGui.QLabel('dt')
        self.h_label = QtGui.QLabel('h')
        self.f_label = QtGui.QLabel('freq')

        self.l_spin = pg.SpinBox(value=self.model.l, step=0.01, siPrefix=False, suffix='s')
        self.h_spin = pg.SpinBox(value=self.model.h, step=0.01, siPrefix=False)
        self.f_spin = pg.SpinBox(value=self.model.f, step=1, siPrefix=False)
        self.reset_params_button = QtGui.QPushButton('Reset to Defaults')
        self.reinit_button = QtGui.QPushButton('Restart Model')
        self.model_params_layout = QtGui.QHBoxLayout()
        self.model_params_layout.addWidget(self.l_label)
        self.model_params_layout.addWidget(self.l_spin)
        self.model_params_layout.addWidget(self.h_label)
        self.model_params_layout.addWidget(self.h_spin)
        self.model_params_layout.addWidget(self.f_label)
        self.model_params_layout.addWidget(self.f_spin)
        self.model_params_layout.addWidget(self.reset_params_button)
        self.model_params_layout.addWidget(self.reinit_button)

        self.label = QtGui.QLabel(f'Current Slice: {self.current_slice}/{self.data.shape[0] - 1}')
        self.label.setGeometry(100, 200, 100, 100)


        self.arrays_to_vis = [QtGui.QRadioButton('P'), QtGui.QRadioButton('r'), QtGui.QRadioButton('ro'), QtGui.QRadioButton('c'), QtGui.QRadioButton('K')]
        self.arrays_to_vis[0].setChecked(True)
        self.radio_layout = QtGui.QHBoxLayout()

        for rad in self.arrays_to_vis:
            self.radio_layout.addWidget(rad)
            rad.toggled.connect(self.array_to_vis_changed)

        self.mapping = {
            'P' : self.model.P,
            'r' : self.model.r,
            'ro': self.model.ro,
            'c' : self.model.c,
            'K' : self.model.K,
        }


        self.layout = QtGui.QVBoxLayout()

        self.glayout = pg.GraphicsLayoutWidget()
        self.glayout.ci.layout.setContentsMargins(0, 0, 0, 0)
        self.img = pg.ImageItem(border='b')
        self.img.setImage(self.data[self.current_slice], autoLevels=True)
        self.view = self.glayout.addViewBox(lockAspect=True, enableMouse=False)
        self.view.addItem(self.img)


        #--------------------------- signal plots ------------------------
        plots_font = QtGui.QFont()
        fontsize = 9
        plots_font.setPixelSize(fontsize)
        plots_height = 150

        self.source_plot = pg.PlotWidget(title=f'Source Signal at P[{self.model.A}, {self.model.B}, {self.model.C}]')
        self.source_plot.showGrid(x=True, y=True, alpha=0.1)
        # self.fft_widget.setLogMode(x=True, y=False)
        # self.fft_widget.setYRange(0, 0.1) # w\o np.log(a)
        # self.fft_widget.setYRange(-15, 0) # w/ np.log(a)
        self.source_plot.getAxis('bottom').setStyle(tickTextOffset = fontsize)
        self.source_plot.getAxis('left').setStyle(tickTextOffset = fontsize)
        self.source_plot.getAxis('bottom').tickFont = plots_font
        self.source_plot.getAxis('left').tickFont = plots_font
        self.source_plot.setMaximumHeight(plots_height)
        self.source_curve = self.source_plot.plot(pen='b')


        self.observ_plot = pg.PlotWidget(title=f'Observable Signal at P[{self.model.oA}, {self.model.oB}, {self.model.oC}]')
        self.observ_plot.showGrid(x=True, y=True, alpha=0.1)
        self.observ_plot.getAxis('bottom').setStyle(tickTextOffset = fontsize)
        self.observ_plot.getAxis('left').setStyle(tickTextOffset = fontsize)
        self.observ_plot.getAxis('bottom').tickFont = plots_font
        self.observ_plot.getAxis('left').tickFont = plots_font
        self.observ_plot.setMaximumHeight(plots_height)
        self.observ_curve = self.observ_plot.plot(pen='r')
        #----------------------------------------------------------------

        self.step_layout = QtGui.QHBoxLayout()
        self.steps_label = QtGui.QLabel('Number of steps: ')
        self.steps_spin = QtGui.QSpinBox()
        self.steps_spin.setRange(1, 100)
        self.steps_spin.setValue(1)
        self.steps_spin.setMaximumSize(100, 50)
        # self.steps_spin.setGeometry(QtCore.QRect(10, 10, 50, 21))
        self.step_button = QtGui.QPushButton('Step')
        # self.step_button.setMaximumSize(100, 50)
        self.step_button.setMaximumWidth(100)
        self.steps_progress_bar = QtGui.QProgressBar()
        self.step_layout.addWidget(self.steps_label)
        self.step_layout.addWidget(self.steps_spin)
        self.step_layout.addWidget(self.step_button)
        self.step_layout.addWidget(self.steps_progress_bar)
        
        self.progress_bar = QtGui.QProgressBar()
        self.progress_bar.setValue(self.current_slice / (self.data.shape[0] - 1) * 100)

        self.slice_slider = QtGui.QSlider()
        self.slice_slider.setOrientation(QtCore.Qt.Horizontal)
        self.slice_slider.setRange(0, self.data.shape[0] - 1)
        self.slice_slider.setValue(self.current_slice)
        self.slice_slider.setTickPosition(QtGui.QSlider.TicksBelow)
        self.slice_slider.setTickInterval(1)


        self.layout.addLayout(self.model_params_layout)
        self.layout.addLayout(self.radio_layout)
        self.layout.addWidget(self.label)
        # self.layout.addWidget(self.progress_bar)
        self.layout.addWidget(self.slice_slider)
        self.layout.addWidget(self.glayout)
        self.layout.addWidget(self.source_plot)
        self.layout.addWidget(self.observ_plot)
        self.layout.addLayout(self.step_layout)

        self.setLayout(self.layout)

        self.setGeometry(0, 0, 600, 900)
        self.show()

    def qt_connections(self):
        self.step_button.clicked.connect(self.do_steps)
        self.l_spin.valueChanged.connect(self.l_spin_value_changed)
        self.h_spin.valueChanged.connect(self.h_spin_value_changed)
        self.f_spin.valueChanged.connect(self.f_spin_value_changed)
        self.reset_params_button.clicked.connect(self.reset_params)
        self.reinit_button.clicked.connect(self.reinit_model)
        self.slice_slider.valueChanged.connect(self.slice_slider_changed)
        self.steps_state.connect(self.update_steps_progress_bar)

    def l_spin_value_changed(self):
        self.model.l = self.l_spin.value()

    def h_spin_value_changed(self):
        self.model.h = self.h_spin.value()
    
    def f_spin_value_changed(self):
        self.model.f = self.f_spin.value()

    @QtCore.pyqtSlot(int)
    def update_steps_progress_bar(self, current_step):
        self.steps_progress_bar.setValue(current_step / self.steps_spin.value() * 100)
        QApplication.processEvents() 

    def reset_params(self):
        # self.model = LungsModel()
        self.l_spin.setValue(LungsModel.l_default)
        self.h_spin.setValue(LungsModel.h_default)
        self.f_spin.setValue(LungsModel.f_default)
        # self.data = self.model.P
        # self.current_slice = self.model.A
        # self.img.setImage(self.data[self.current_slice], autoLevels=True)

    def reinit_model(self):
        self.model = LungsModel(self.l_spin.value(), self.h_spin.value(), self.f_spin.value())
        self.data = self.model.P
        self.current_slice = self.model.A
        self.img.setImage(self.data[self.current_slice], autoLevels=True)

    def array_to_vis_changed(self):
        for r in self.arrays_to_vis:
            if r.isChecked():
                self.data = self.mapping[r.text()]
                self.img.setImage(self.data[self.current_slice], autoLevels=True)

    def do_steps(self):
        for i in range(self.steps_spin.value()):
            self.model.step()
            self.img.setImage(self.data[self.current_slice], autoLevels=True)
            self.steps_state.emit(i + 1)
        self.steps_state.emit(0)       

    def wheelEvent(self,event):
        self.current_slice = np.clip(self.current_slice + np.sign(event.angleDelta().y()), 0, self.data.shape[0] - 1)
        self.label.setText(f'Current Slice: {self.current_slice}/{self.data.shape[0] - 1}')
        self.img.setImage(self.data[self.current_slice], autoLevels=True)
        # self.progress_bar.setValue(self.current_slice / (self.data.shape[0] - 1) * 100)
        self.slice_slider.setValue(self.current_slice)
        print(np.mean(self.data[self.current_slice]))

    def keyPressEvent(self, event):
        if type(event) == QtGui.QKeyEvent and event.key() == QtCore.Qt.Key_Up:
            self.do_steps()
            #here accept the event and do something
            # self.record_values_button_clicked()
            event.accept()
        else:
            event.ignore()

    def slice_slider_changed(self):
        self.current_slice = self.slice_slider.value()
        self.label.setText(f'Current Slice: {self.current_slice}/{self.data.shape[0] - 1}')
        self.img.setImage(self.data[self.current_slice], autoLevels=True)
        self.progress_bar.setValue(self.current_slice / (self.data.shape[0] - 1) * 100)


app = QtGui.QApplication(sys.argv)
gui = AppGUI()
signal.signal(signal.SIGINT, signal.SIG_DFL)
sys.exit(app.exec())

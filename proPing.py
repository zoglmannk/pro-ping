import sys
import socket
import subprocess
import threading
import time
import datetime
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from collections import deque
from PyQt5.QtWidgets import QSizePolicy,  QHBoxLayout, QWidget, QLabel
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout
from PyQt5.QtCore import Qt, QSize, QTimer
from PyQt5.QtCore import pyqtSignal, QObject, QEvent
from PyQt5.QtGui import QPainter, QColor, QFont
import threading

class PingThread(QObject):
    update_signal = pyqtSignal(tuple)

    def __init__(self, host, instance_num, ping_frequency):
        super().__init__()
        self.host = host
        self.instance_num = instance_num
        self.ping_frequency = ping_frequency
        self.running = True


    def run(self):
        while self.running:
            one_billion = 1000000000
            current_time_ns = time.time_ns()
            next_sleep_time_ns = (int((current_time_ns / one_billion) + 1) + (1 / self.ping_frequency * self.instance_num)) * one_billion
            sleep_for = (next_sleep_time_ns - current_time_ns) / one_billion

            result = self.ping(self.host)
            timestamp = time.time()
            self.emit_update((timestamp, result))
            time.sleep(sleep_for)

    def emit_update(self, result):
        self.update_signal.emit(result)

    def ping(self, host):
        try:
            output = subprocess.check_output(['ping', '-c 1', '-t 1', '-q', host],
                                             stderr=subprocess.STDOUT,
                                             universal_newlines=True)
            packet_loss_info = [line for line in output.split('\n') if 'packet loss' in line]
            if packet_loss_info:
                packet_loss = packet_loss_info[0].split('%')[0].split(' ')[-1]
                return packet_loss
            else:
                return 'N/A'
        except subprocess.CalledProcessError:
            return 'Error'

    def stop(self):
        self.running = False

class PacketLossIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.packet_loss = 0
        self.current_color = self.get_color_based_on_packet_loss(self.packet_loss)

        # Set size policy to be expandable but maintain aspect ratio
        sizePolicy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        sizePolicy.setHeightForWidth(True)
        self.setSizePolicy(sizePolicy)

    def set_packet_loss(self, packet_loss):
        new_color = self.get_color_based_on_packet_loss(packet_loss)
        if new_color != self.current_color:
            self.packet_loss = packet_loss
            self.current_color = new_color
            self.update()  # Only update if the color actually changes

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self.current_color))

    def resizeEvent(self, event):
        # Ensure the widget maintains a square shape
        size = min(self.width(), self.height())
        self.setFixedSize(size, size)

    def get_color_based_on_packet_loss(self, packet_loss):
        # Define RGB values for yellow, orange, red, and grey
        yellow = (255, 255, 0)
        orange = (255, 165, 0)
        red = (255, 0, 0)
        grey = (169, 169, 169)

        def interpolate(color1, color2, factor):
            # Interpolate between two colors
            return tuple(int(a + (b - a) * factor) for a, b in zip(color1, color2))

        def mix_with_grey(color, factor):
            # Mix a color with grey to reduce saturation
            return interpolate(color, grey, factor)

        if packet_loss >= 30:
            color = red
        elif packet_loss >= 10:
            # Smooth transition from orange to red
            color = interpolate(orange, red, (packet_loss - 10) / 20)
        elif packet_loss >= 3:
            # Smooth transition from yellow to orange
            color = interpolate(yellow, orange, (packet_loss - 3) / 7)
        else:
            color = grey

        # Mix the chosen color with grey to reduce saturation
        less_saturated_color = mix_with_grey(color, 0.3)  # 30% grey

        # Convert to hex color code
        return f'#{less_saturated_color[0]:02x}{less_saturated_color[1]:02x}{less_saturated_color[2]:02x}'

class HeartbeatIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_color = QColor("lightgrey")
        self.setFixedSize(10, 10)  # Fixed size for the heartbeat indicator

    def toggle_color(self):
        if self.active_color.name() == QColor("darkgrey").name():
            self.active_color = QColor("lightgrey")
        else:
            self.active_color = QColor("darkgrey")
        self.update()  # Trigger a repaint

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.active_color)


class NetMonitorPro(QMainWindow):
    def __init__(self, ping_host):
        super().__init__()
        self.ping_host = ping_host
        self.ping_frequency = 10
        self.num_of_bars_in_chart = 60
        self.seconds_in_minute = 60
        self.max_minute_interval = 5
        self.ping_results = deque(maxlen=self.ping_frequency * self.num_of_bars_in_chart * self.max_minute_interval * self.seconds_in_minute)  # Store last 300 minutes of data
        self.ping_threads = []  # Keep track of all ping threads, so we can shut them down
        self.start_time = datetime.datetime.now()

        # Add variables to track the last update time for the 1m and 5m charts, so we only update them when needed
        self.last_update_time_1m = datetime.datetime.now()
        self.last_update_time_5m = datetime.datetime.now()

        # Initialize packet loss history for each time frame
        self.packet_loss_history_1s = deque(maxlen=self.num_of_bars_in_chart)  # each bar represents 1 second
        self.packet_loss_history_1m = deque(maxlen=self.num_of_bars_in_chart)  # each bar represents 1 minute
        self.packet_loss_history_5m = deque(maxlen=self.num_of_bars_in_chart)  # each bar represents 5 minutes

        self.initChart()
        self.initUI()


        for n in range(self.ping_frequency):
            time.sleep(1 / self.ping_frequency)
            self.ping_thread = PingThread(self.ping_host, n, self.ping_frequency)
            self.ping_thread.update_signal.connect(self.wrapper_update_metrics)
            self.thread = threading.Thread(target=self.ping_thread.run)
            thread = threading.Thread(target=self.ping_thread.run, name=f'PingThread-{n + 1}')
            thread.start()
            self.ping_threads.append((self.ping_thread, thread))

        # Set up a timer for updating the charts
        self.chart_and_label_update_timer = QTimer(self)
        self.chart_and_label_update_timer.timeout.connect(self.updateChartLabelsAndRuntime)
        self.chart_and_label_update_timer.start(1000)  # Update every 1000 milliseconds (1 second)

    def closeEvent(self, event):
        # Signal the thread to stop
        self.ping_thread.stop()

        # Wait for the thread to finish
        for ping_thread, thread in self.ping_threads:
            ping_thread.stop()
            thread.join(timeout=1.5)  # Add a reasonable timeout

        # Stop any running timers
        self.chart_and_label_update_timer.stop()
        self.heartbeat_timer.stop()

        # Ensure the application quits
        QApplication.quit()

        # Call the base class implementation
        super().closeEvent(event)

    def initUI(self):
        # Set main window properties
        self.setWindowTitle('ProPing')
        self.setGeometry(300, 300, 500, 650)

        # Create central widget and layout
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        # Create a vertical layout for the entire window
        main_layout = QVBoxLayout(central_widget)

        # Create a horizontal layout for the indicator and labels
        top_layout = QHBoxLayout()

        # Create and add the packet loss indicator widget
        self.packet_loss_indicator = PacketLossIndicator()
        top_layout.addWidget(self.packet_loss_indicator)

        # Create a layout for labels
        labels_layout = QVBoxLayout()

        # Add widgets for packet loss
        self.packet_loss_1s_label = QLabel('1-Second Packet Loss: 0%', self)
        labels_layout.addWidget(self.packet_loss_1s_label)

        self.packet_loss_1m_label = QLabel('1-Minute Packet Loss: 0%', self)
        labels_layout.addWidget(self.packet_loss_1m_label)

        self.packet_loss_5m_label = QLabel('5-Minute Packet Loss: 0%', self)
        labels_layout.addWidget(self.packet_loss_5m_label)

        # Add the labels layout to the horizontal layout
        top_layout.addLayout(labels_layout)

        # Add the top horizontal layout to the main vertical layout
        main_layout.addLayout(top_layout, 1)

        # Create a new QVBoxLayout for the chart and its title
        chart_layout = QVBoxLayout()
        chart_layout.setSpacing(0)  # Remove spacing between items in this layout

        # Add a title label for the charts
        chart_title_label = QLabel('Percent Packet Loss', self)
        chart_title_label.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setPointSize(24)
        font.setBold(True)
        chart_title_label.setFont(font)
        chart_title_label.setStyleSheet("background-color: white; padding-top: 15px;")

        # Set size policy for the title label to be fixed
        sizePolicy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        chart_title_label.setSizePolicy(sizePolicy)

        chart_layout.addWidget(chart_title_label)

        # Initialize and add the chart to the main layout with more stretch
        self.initChart()
        chart_layout.addWidget(self.canvas)

        main_layout.addLayout(chart_layout, 4)  # Greater stretch factor for the chart

        self.heartbeat_indicator = HeartbeatIndicator(self)
        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.timeout.connect(self.heartbeat_indicator.toggle_color)
        self.heartbeat_timer.start(500)  # Toggle color every 500 milliseconds

        self.runtime_label = QLabel("Runtime: 0 seconds", self)

        # Add an uptime label at the bottom
        runtime_layout = QHBoxLayout()
        runtime_layout.addWidget(self.heartbeat_indicator)
        runtime_layout.addWidget(self.runtime_label)
        main_layout.addLayout(runtime_layout)

    def initChart(self):
        # Initialize Matplotlib figure and axes for three subplots
        self.figure, self.axes = plt.subplots(3, 1, figsize=(5, 4))

        # Create bar plot objects
        self.bar_plot_1s = self.axes[0].bar(range(self.num_of_bars_in_chart), [0] * self.num_of_bars_in_chart, color='b')
        self.bar_plot_1m = self.axes[1].bar(range(self.num_of_bars_in_chart), [0] * self.num_of_bars_in_chart, color='g')
        self.bar_plot_5m = self.axes[2].bar(range(self.num_of_bars_in_chart), [0] * self.num_of_bars_in_chart, color='r')

        # Set initial y-axis limits and titles
        for i, ax in enumerate(self.axes):
            if i == 0:
                ax.set_title("1-Second Intervals")
                # Set y-axis ticks for 1-second intervals
                ax.yaxis.set_major_locator(ticker.LinearLocator(numticks=3))
            elif i == 1:
                ax.set_title("1-Minute Intervals")
                # Set y-axis ticks for 1-minute intervals
                ax.yaxis.set_major_locator(ticker.LinearLocator(numticks=3))  # For example, 6 ticks
            else:
                ax.set_title("5-Minute Intervals")
                # Set x-axis ticks for 5-minute intervals
                ax.yaxis.set_major_locator(ticker.LinearLocator(numticks=3))  # For example, 6 ticks

        # Apply tight_layout with increased padding
        self.figure.tight_layout(pad=3.0, h_pad=1.5, w_pad=1.0)  # Adjust padding as needed

        self.canvas = FigureCanvas(self.figure)

    def update_runtime(self):
        # Calculate uptime
        current_time = datetime.datetime.now()
        runtime_duration = current_time - self.start_time
        total_seconds = int(runtime_duration.total_seconds())

        # Format runtime into a readable string
        if total_seconds < 60:
            runtime_text = f"Runtime: {total_seconds} seconds"
        elif total_seconds < 3600:
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            runtime_text = f"Runtime: {minutes} minutes, {seconds} seconds"
        elif total_seconds < 86400:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            runtime_text = f"Runtime: {hours} hours, {minutes} minutes"
        else:
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            runtime_text = f"Runtime: {days} days, {hours} hours"

        # Update the label
        self.runtime_label.setText(runtime_text)

    def updateChartLabelsAndRuntime(self):
        self.update_labels()
        self.updateChart()
        self.update_runtime()

    def updateChart(self):
        current_time = datetime.datetime.now()

        # Update the 1-second interval chart
        self.update_history(self.packet_loss_history_1s, 1, self.num_of_bars_in_chart)
        plotable_packet_loss_history_1s = [0 if x == None else x for x in list(self.packet_loss_history_1s)]
        for rect, h in zip(self.bar_plot_1s, plotable_packet_loss_history_1s):
            rect.set_height(h)
        self.axes[0].set_ylim(0, max(plotable_packet_loss_history_1s) + 1)  # Adjust y-axis

        # Update the 1-minute interval chart only if a minute has passed
        if (current_time - self.last_update_time_1m).total_seconds() >= 60:
            self.update_history(self.packet_loss_history_1m, 60, self.num_of_bars_in_chart)
            plotable_packet_loss_history_1m = [0 if x == None else x for x in list(self.packet_loss_history_1m)]
            for rect, h in zip(self.bar_plot_1m, plotable_packet_loss_history_1m):
                rect.set_height(h)
            self.axes[1].set_ylim(0, max(plotable_packet_loss_history_1m) + 1)  # Adjust y-axis
            self.last_update_time_1m = current_time

        # Update the 5-minute interval chart only if five minutes have passed
        if (current_time - self.last_update_time_5m).total_seconds() >= 300:
            self.update_history(self.packet_loss_history_5m, 300, self.num_of_bars_in_chart)
            plotable_packet_loss_history_5m = [0 if x is None else x for x in list(self.packet_loss_history_5m)]
            for rect, h in zip(self.bar_plot_5m, plotable_packet_loss_history_5m):
                rect.set_height(h)
            self.axes[2].set_ylim(0, max(plotable_packet_loss_history_5m) + 1)  # Adjust y-axis
            self.last_update_time_5m = current_time

        # Apply tight_layout with increased padding
        self.figure.tight_layout(pad=3.0, h_pad=1.5, w_pad=1.0)  # Adjust padding as needed

        self.canvas.draw_idle()  # Efficiently redraw only the changed elements

    def wrapper_update_metrics(self, result):
        QApplication.instance().postEvent(self, CustomEvent(result))

    def update_metrics(self, result_tuple):
        current_time = time.time()

        # Check if result is 'Error' or convert it to a float
        timestamp, result = result_tuple
        packet_loss_value = 100.0 if result == 'Error' else float(result)

        self.ping_results.append((current_time, packet_loss_value))

        # Update packet loss indicator color
        latest_packet_loss = self.calculate_packet_loss(1)
        self.packet_loss_indicator.set_packet_loss(latest_packet_loss)


    def update_history(self, history_deque, interval_seconds, num_intervals):
        current_time = datetime.datetime.now()
        new_history = [None] * num_intervals
        current_minute = current_time.minute
        current_hour = current_time.hour

        new_history = []
        for i in range(num_intervals):
            # Calculate the start and end of each interval based on the current time
            if interval_seconds == 1:  # For 1-second intervals
                interval_end_time = current_time.replace(microsecond=0) - datetime.timedelta(seconds=i)
            elif interval_seconds == 60:  # For 1-minute intervals
                interval_end_time = current_time.replace(second=0, microsecond=0) - datetime.timedelta(minutes=i)
            elif interval_seconds == 300:  # For 5-minute intervals
                five_minute_block = (current_minute // 5) * 5
                interval_end_time = current_time.replace(minute=five_minute_block, second=0,
                                                         microsecond=0) - datetime.timedelta(minutes=5 * i)

            interval_start_time = interval_end_time - datetime.timedelta(seconds=interval_seconds)

            # Convert to timestamp for comparison
            interval_start_timestamp = interval_start_time.timestamp()
            interval_end_timestamp = interval_end_time.timestamp()

            # Calculate average packet loss for the interval
            interval_data = [loss for timestamp, loss in self.ping_results if
                             interval_start_timestamp <= timestamp < interval_end_timestamp]
            if len(interval_data) == 0:
                new_history.append(None)
            else:
                average_loss = sum(interval_data) / len(interval_data) if interval_data else 0.0
                new_history.append(average_loss)

        history_deque.clear()
        history_deque.extend(new_history)

    def update_labels(self):
        # Function to calculate average, min, and max from deque
        def get_stats(deque_data):
            valid_data = [x for x in deque_data if x is not None]
            if valid_data:
                avg = sum(valid_data) / len(valid_data)
                max_val = max(valid_data)
                return avg, max_val
            else:
                return 0.0, 0.0

        # Update labels using the data from the deques
        avg_1s, max_1s = get_stats(self.packet_loss_history_1s)
        self.packet_loss_1s_label.setText(
            f'1-Second Packet Loss: Avg {avg_1s:.1f}% / Max {max_1s:.1f}%')

        avg_1m, max_1m = get_stats(self.packet_loss_history_1m)
        self.packet_loss_1m_label.setText(
            f'1-Minute Packet Loss: Avg {avg_1m:.1f}% / Max {max_1m:.1f}%')

        avg_5m, max_5m = get_stats(self.packet_loss_history_5m)
        self.packet_loss_5m_label.setText(
            f'5-Minute Packet Loss: Avg {avg_5m:.1f}% / Max {max_5m:.1f}%')


    def calculate_packet_loss(self, seconds):
        current_time = time.time()
        start_time = current_time - seconds
        relevant_data = [loss for timestamp, loss in self.ping_results if timestamp >= start_time]

        if relevant_data:
            packet_loss_sum = sum(relevant_data)
            packet_loss = round(packet_loss_sum / len(relevant_data), 2)
            return packet_loss

        return 0.0

    def customEvent(self, event):
        self.update_metrics(event.data)

class CustomEvent(QEvent):
    def __init__(self, data):
        super().__init__(QEvent.User)
        self.data = data

    def closeEvent(self, event):
        self.ping_thread.stop()
        self.thread.join()
        super().closeEvent(event)

def main():
    app = QApplication(sys.argv)

    if len(sys.argv) < 2:
        print("Error: Ping host not provided. Usage: python monitor.py [ping_host]")
        sys.exit(1)
    hostname = sys.argv[1]

    # Try to resolve the hostname
    try:
        socket.gethostbyname(hostname)
    except socket.gaierror:
        print(f"Error: The hostname '{hostname}' could not be resolved. Please provide a valid hostname or IP address.")
        sys.exit(1)

    ex = NetMonitorPro(hostname)
    ex.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
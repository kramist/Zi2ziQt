import math
import os
import shutil
import sys
import paramiko
from pathlib import Path

from PIL import Image
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QTextEdit, QPushButton, QLabel, QSpinBox, QFileDialog,
    QHBoxLayout, QProgressBar, QMessageBox, QComboBox
)
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt, QThread, pyqtSignal


class SSHWorker(QThread):
    progress_updated = pyqtSignal(int)
    finished = pyqtSignal(list)
    error_occurred = pyqtSignal(str)
    output_received = pyqtSignal(str)

    def __init__(self, config, command, local_dir):
        super().__init__()
        self.config = config
        self.command = command
        self.local_dir = local_dir

    def run(self):
        try:
            # 创建SSH客户端
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.output_received.emit("SSH连接成功")  # 测试信号
            # 连接服务器
            ssh.connect(
                self.config['hostname'],
                port=self.config['port'],
                username=self.config['username'],
                password=self.config['password']
            )

            # 执行命令
            stdin, stdout, stderr = ssh.exec_command(self.command)

            # 监控进度
            while not stdout.channel.exit_status_ready():
                line = stdout.readline()
                self.output_received.emit(line.strip())
                if "Progress:" in line:
                    progress = int(line.split(":")[1].strip())
                    self.progress_updated.emit(progress)
            error_output = stderr.read().decode('utf-8')
            if error_output:
                self.error_occurred.emit(error_output)

            # 传输文件
            sftp = ssh.open_sftp()
            # 在SSHWorker中添加路径验证
            remote_dir = "/hy-tmp/zi2zi-chain-master/experiment/infer_sentence/0"
            try:
                sftp.stat(remote_dir)  # 验证目录是否存在
            except FileNotFoundError:
                self.error_occurred.emit(f"远程目录不存在: {remote_dir}")
                return
            files = sftp.listdir(remote_dir)

            downloaded = []
            for f in files:
                if f.endswith(".png"):
                    remote_file = f"{remote_dir}/{f}"
                    local_file = self.local_dir / f
                    sftp.get(remote_file, str(local_file))
                    downloaded.append(str(local_file))

            self.finished.emit(downloaded)

        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            ssh.close()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GAN文字生成工具")
        self.setGeometry(100, 100, 800, 600)

#ssh -p 32170 root@i-2.gpushare.com
        # 服务器配置
        self.server_config = {
            'hostname': 'i-2.gpushare.com',
            'port': 32170,
            'username': 'root',
            'password': 'Y9xy3tbXbeBb5X72WcACPsqFnTht5He3'
        }

        # 初始化成员变量
        self.current_images = []
        self.current_index = 0

        self.init_ui()

    def init_ui(self):
        main_widget = QWidget()
        layout = QVBoxLayout()

        # 文字输入
        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("输入要生成的文字（最多100字）")
        layout.addWidget(QLabel("输入文字:"))
        layout.addWidget(self.text_input)

        # 参数设置
        param_layout = QHBoxLayout()
        # 模型步数下拉框
        self.resume_combo = QComboBox()
        for step in range(0, 25201, 500):  # 生成0-5000，每500递增
            self.resume_combo.addItem(str(step), userData=step)
        self.resume_combo.setCurrentIndex(self.resume_combo.findData(25000))  # 默认选中25000
        param_layout.addWidget(QLabel("模型步数:"))
        param_layout.addWidget(self.resume_combo)

        # 字体选择下拉框
        self.font_combo = QComboBox()
        self.font_combo.addItem("隶书")
        self.font_combo.addItem("宋徽宗瘦金体")
        self.font_combo.addItem("米芾行书")
        self.font_combo.addItem("华文行书")
        param_layout.addWidget(QLabel("字体选择:"))
        param_layout.addWidget(self.font_combo)


        # 批大小保持不变
        self.batch_spin = QSpinBox()
        self.batch_spin.setRange(1, 32)
        self.batch_spin.setValue(32)
        param_layout.addWidget(QLabel("批大小:"))
        param_layout.addWidget(self.batch_spin)

        layout.addLayout(param_layout)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # 生成按钮
        self.generate_btn = QPushButton("开始生成")
        self.generate_btn.clicked.connect(self.start_generation)
        layout.addWidget(self.generate_btn)

        # 图片显示
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.image_label)

        # 底部按钮
        btn_layout = QHBoxLayout()
        self.prev_btn = QPushButton("上一张")
        self.prev_btn.clicked.connect(self.show_prev_image)
        self.next_btn = QPushButton("下一张")
        self.next_btn.clicked.connect(self.show_next_image)
        btn_layout.addWidget(self.prev_btn)
        btn_layout.addWidget(self.next_btn)
        layout.addLayout(btn_layout)

        # 保存，分批量保存和单张保存
        self.save_btn = QPushButton("保存")
        self.save_btn.clicked.connect(self.save_images)
        btn_layout.addWidget(self.save_btn)

        self.save_batch_btn = QPushButton("批量保存")
        self.save_batch_btn.clicked.connect(self.save_images_batch)
        btn_layout.addWidget(self.save_batch_btn)

        # 自动拼接图片
        self.auto_btn = QPushButton("自动拼接")
        self.auto_btn.clicked.connect(self.auto_splicing_images)
        btn_layout.addWidget(self.auto_btn)


        main_widget.setLayout(layout)
        self.setCentralWidget(main_widget)

        self.update_buttons()

    def start_generation(self):
        text = self.text_input.toPlainText().strip()
        resume_step = self.resume_combo.currentData()
        if not text:
            QMessageBox.warning(self, "错误", "请输入要生成的文字")
            return

        # 创建本地缓存目录
        self.local_dir = Path("temp_results")
        self.local_dir.mkdir(exist_ok=True)

        # 清空旧文件
        for f in self.local_dir.glob("*.png"):
            f.unlink()

        text = self.text_input.toPlainText().strip()

        # 修改后的命令构造方式
        # combined_cmd = f"""
        # bash -c '
        # source /root/anaconda3/etc/profile.d/conda.sh &&
        # conda activate pytorch_env &&
        # cd /home/haverson/zi2zi/zi2zi-chain &&
        # python infer.py \
        #     --experiment_dir experiment \
        #     --gpu_ids cuda:0 \
        #     --batch_size {self.batch_spin.value()} \
        #     --resume {resume_step} \
        #     --from_txt \
        #     --src_font simhei.ttf \
        #     --src_txt "{text}" \
        #     --infer_dir infer_sentence'
        # """
        combined_cmd = f"""
        bash -c '
        cd /hy-tmp/zi2zi-chain-master/ &&
        python infer.py \
            --experiment_dir experiment \
            --gpu_ids cuda:0 \
            --batch_size {self.batch_spin.value()} \
            --resume {resume_step} \
            --from_txt \
            --src_font simhei.ttf \
            --src_txt "{text}" \
            --infer_dir infer_sentence'
        """

        # 启动工作线程
        print(combined_cmd)

        self.worker = SSHWorker(
            self.server_config,
            combined_cmd,
            self.local_dir
        )
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.finished.connect(self.show_results)
        self.worker.error_occurred.connect(self.show_error)
        self.worker.start()

        # 更新UI状态
        self.progress_bar.setVisible(True)
        self.generate_btn.setEnabled(False)

        # 调试
        self.worker.error_occurred.connect(lambda msg: print(f"Error: {msg}"))  # 临时调试
        self.worker.start()

    def update_progress(self, value):
        self.progress_bar.setValue(value)

    # 在show_results方法中保持现有设置
    def show_results(self, files):
        self.progress_bar.setVisible(False)
        self.generate_btn.setEnabled(True)
        self.current_images = files
        self.current_index = 0  # 保持重置逻辑

        if not files:
            QMessageBox.information(self, "提示", "没有生成任何图片")
            return

        self.show_image(0)
        self.update_buttons()

    def show_error(self, msg):
        self.progress_bar.setVisible(False)
        self.generate_btn.setEnabled(True)
        QMessageBox.critical(self, "错误", f"生成失败:\n{msg}")

    def show_image(self, index):
        if 0 <= index < len(self.current_images):
            pixmap = QPixmap(self.current_images[index])
            self.image_label.setPixmap(
                pixmap.scaled(600, 400, Qt.KeepAspectRatio)
            )
            self.current_index = index

    def show_prev_image(self):
        self.show_image(self.current_index - 1)
        self.update_buttons()

    def show_next_image(self):
        self.show_image(self.current_index + 1)
        self.update_buttons()

    def update_buttons(self):
        self.prev_btn.setEnabled(self.current_index > 0)
        self.next_btn.setEnabled(
            self.current_index < len(self.current_images) - 1
        )

    def save_images(self):
        if self.current_images:
            file_path = self.current_images[self.current_index]
            new_file_path = Path("results") / f"{self.current_index}.png"
            new_file_path.parent.mkdir(exist_ok=True)
            shutil.copy(file_path, new_file_path)
            QMessageBox.information(self, "提示", "图片已保存到本地缓存目录{}".format(new_file_path))

    def save_images_batch(self):
        if self.current_images:
            for i, file_path in enumerate(self.current_images):
                new_file_path = Path("results") / f"{i}.png"
                new_file_path.parent.mkdir(exist_ok=True)
                shutil.copy(file_path, new_file_path)
            QMessageBox.information(self, "提示", "所有图片已保存到本地缓存目录{}".format(new_file_path))

    def auto_splicing_images(self):
        """生成的图片512x512，位于temp_results，每五张一排，拼接成一张大图"""
        if not self.current_images:
            return

        # 确保输出目录存在
        os.makedirs("spliced_results", exist_ok=True)

        # 加载所有图片并统一尺寸[1,7](@ref)
        images = []
        for file in self.current_images:
            img = Image.open(file)
            # 统一尺寸为512x512[1](@ref)
            if img.size != (512, 512):
                img = img.resize((512, 512), Image.LANCZOS)  # 使用高质量缩放[7](@ref)
            images.append(img)

        # 计算布局参数
        num_images = len(images)
        cols = 5  # 每排固定5张
        rows = math.ceil(num_images / cols)  # 计算需要的行数

        # 创建新画布[1,6](@ref)
        new_width = 512 * cols
        new_height = 512 * rows
        new_image = Image.new("RGB", (new_width, new_height), (255, 255, 255))  # 白色背景

        # 拼接图片[1,6](@ref)
        for i, img in enumerate(images):
            row = i // cols  # 计算行位置
            col = i % cols  # 计算列位置
            x = col * 512
            y = row * 512
            new_image.paste(img, (x, y))

        # 保存结果[1](@ref)
        output_path = "spliced_results/spliced.png"
        new_image.save(output_path)
        return output_path


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
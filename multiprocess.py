import multiprocessing as mp
import os
import sys
import suntools
import time
import numpy as np
from astropy.io import fits
import scipy.signal as signal
import urllib.error as uEr
import config
import matplotlib.pyplot as plt

# 读入配置文件 引入参数
read_dir = config.data_dir_path
out_dir = config.save_dir_path
multiprocess_count = 1
if config.multiprocess_count != 'default':
    multiprocess_count = config.multiprocess_count
else:
    multiprocess_count = mp.cpu_count() - 4
print('多核并行数:' + str(multiprocess_count))


# 读取数据文件夹所有文件
def read_fits_directory():
    arr = []
    arr = os.listdir(read_dir)
    if len(arr) == 0:
        raise OSError
    return arr


# 此处的数据均未做共享处理，因为共享数据量并不是很大，在LINUX环境下使用multiprocess并fork()将直接复制此些全局变量
# 预读输入目录
try:
    data_file_lst = read_fits_directory()
except OSError:
    print('没有获得原始数据文件，请检查config中的读入数据目录')
    sys.exit("程序终止")
print('文件总数为: ' + str(len(data_file_lst)))

# 读取暗场文件
temp_img = None
dark_img = None
try:
    print("正在读取原始暗场文件")
    temp_img = fits.open(config.dark_fits_name)
except uEr.URLError:
    print("Error: 暗场文件未找到, 请检查config文件或存放目录")
    sys.exit("程序终止")
except OSError:
    print("Error: 暗场文件读取发生错误, 请检查文件读取权限")
    sys.exit("程序终止")
if temp_img is not None:
    dark_img = np.array(temp_img[0].data, dtype=float)
    dark_img = suntools.change(dark_img)

# 平场需要以日心图片作为基准进行平移矫正 再进行谱线弯曲矫正
flat_img = None
try:
    print("正在读取原始平场文件")
    temp_img = fits.open(config.flat_fits_name)
except uEr.URLError:
    print("Error: 原始平场文件未找到, 请检查config文件或存放目录")
    sys.exit("程序终止")
except OSError:
    print("Error: 原始平场文件读取发生错误, 请检查文件读取权限")
    sys.exit("程序终止")
if temp_img is not None:
    flat_img = np.array(temp_img[0].data, dtype=float)

# 读取经过日心的图片 作为基准
# 创建一个list 获取每个序列的基准 并以此基准获得矫正后的平场与吸收系数
standard_list = []
flat_abortion_list = []
# 读取标准太阳光谱数据
sun_std = suntools.get_Sunstd(config.sun_std_name)
for fileName in data_file_lst:
    if int(fileName[24:28]) == config.standard_offset_index:
        standard_list.append(fileName)
try:
    print("正在获取当前文件夹内的所有基准文件, 选择序号为:" + str(config.standard_offset_index))
    print("当前文件夹内共有：" + str(len(standard_list)) + "个序列...")
    # 使用获得的所有基准文件 矫正平场 获得flat_list 与 abortion_list
    for standard_name in standard_list:
        print("矫正平场中...")
        temp_img = fits.open(read_dir + '/' + standard_name)
        standard_img = np.array(temp_img[0].data, dtype=float)
        # 先平移矫正 减去暗场 再谱线弯曲矫正
        flatTemp = suntools.getFlatOffset(flat_img, standard_img)
        flatTemp, temp1, temp2 = suntools.curve_correction(flatTemp - dark_img, config.curve_cor_x0, config.curve_cor_C)
        flatTemp = suntools.getFlat(flatTemp)
        print("序列:" + str(int(standard_name[19:23])) + "矫正完成")
        print("获得标准太阳光谱数据中...")
        # 以标准文件作为基准 计算红蓝移吸收系数
        # 需要先对标注文件进行一系列操作 去暗场 去平场 再进行红蓝移修正
        standard_img = suntools.moveImg(standard_img, -2)
        standard_img, temp1, temp2 = suntools.curve_correction(standard_img - dark_img, config.curve_cor_x0,
                                                               config.curve_cor_C)
        standard_img = suntools.DivFlat(standard_img, flat_img)
        # 获得标准吸收系数
        abortion = suntools.RB_getdata(standard_img, sun_std, temp1, temp2)
        flat_abortion_list.append({
            'index': int(standard_name[19:23]),
            'flatData': flatTemp,
            'abortionData': abortion
        })
except uEr.URLError:
    print("Error: 标准日心校准文件未找到, 请检查config文件或存放目录")
    sys.exit("程序终止")
except OSError:
    print("Error: 标准日心校准文件读取发生错误, 请检查文件读取权限")
    sys.exit("程序终止")

# 读取输出色谱
color_map = suntools.get_color_map(config.color_camp_name)

# 检查输出文件夹是否存在 不存在则创建
if not os.path.exists(out_dir):
    os.mkdir(out_dir)

# 全局进度控制
file_count = mp.Value('i', len(read_fits_directory()))
remaining_count = mp.Value('i', 0)


# 定义target task
# 传入一个文件名，读取此文件名对应的fits文件并对其做曲线矫正
def target_task(filename):
    # 一个标准文件名 如下:
    # RSM 2021   12     22T060105   -   0008-     0001       .fts
    # 012 3456   78     901234567   8   90123     4567       8901
    #     [year] [mon]  [day_seq]       [index]   [position]
    file_year = filename[3:7]
    file_mon = filename[7:9]
    file_day_seq = filename[9:18]
    file_index = filename[19:23]
    file_position = filename[24:28]
    filePath = read_dir + "/" + filename
    file_data = fits.open(filePath)
    image_data = np.array(file_data[0].data, dtype=float)
    # 对fe窗口进行平移
    image_data = suntools.moveImg(image_data, -2)
    # 去暗场
    image_data = image_data - dark_img
    # 谱线弯曲矫正
    image_data, HofH, HofFe = suntools.curve_correction(image_data, config.curve_cor_x0, config.curve_cor_C)
    # 搜索list
    currentFlat = None
    currentAbortion = None
    for dataTemp in flat_abortion_list:
        if dataTemp['index'] == int(file_index):
            currentFlat = dataTemp['flatData']
            currentAbortion = dataTemp['abortionData']
            break
    if currentAbortion is None or currentFlat is None:
        print("文件：" + filename + "未找到平场数据与吸收系数, 请检查文件夹")
        return
    # 去平场
    image_data = suntools.DivFlat(image_data, currentFlat)
    # 红蓝移矫正
    image_data = suntools.RB_repair(image_data, currentAbortion)
    # 滤波
    image_data = signal.medfilt(image_data, kernel_size=config.filter_kernel_size)
    # 转为整型
    image_data = np.array(image_data, dtype=np.int16)
    # 存储FE窗口的fits文件
    primaryHDU = fits.PrimaryHDU(image_data[HofH: HofH + HofFe])
    greyHDU = fits.HDUList([primaryHDU])
    greyHDU.writeto(
        out_dir + "RSM" + file_year + "-" + file_mon + "-" + file_day_seq + "_" + file_index + "_" + file_position + "_" + "FE.fits")
    # 存储HA窗口的fits文件
    primaryHDU = fits.PrimaryHDU(image_data[0: HofH])
    greyHDU = fits.HDUList([primaryHDU])
    greyHDU.writeto(
        out_dir + "RSM" + file_year + "-" + file_mon + "-" + file_day_seq + "_" + file_index + "_" + file_position + "_" + "HA.fits")
    # 进度输出
    remaining_count.value += 1
    greyHDU.close()
    file_data.close()
    print('当前进度:' + str(remaining_count.value) + '/' + str(file_count.value))


def main():
    # 测试消耗时间 时间起点
    time_start = time.time()
    # 获得文件夹列表 读取相关参数
    # 并行处理
    print('开启多核并行处理...')
    pool = mp.Pool(processes=multiprocess_count)
    pool.map(target_task, data_file_lst)
    time_end = time.time()
    print('并行进度已完成，所花费时间为：', (time_end - time_start) / 60, 'min(分钟)')

    # 汇总处理结果
    print("准备写入汇总，生成日像...")
    sum_file_path = config.sum_dir_path
    file_list = os.listdir(out_dir)
    N = len(file_list)
    data = []
    for i in range(N):
        filename = file_list[i]
        print('生成的文件总数为:' + str(N) + '/' + '当前读取文件序号:' + str(i))
        image_file = fits.open(out_dir + "/" + filename)
        if filename[-7: -5] != 'HA':
            continue
        image_data = image_file[0].data
        # 选取图像文件名的最后四个字符作为index
        sun_index = int(filename[-17: -13])
        count = int(filename[-12: -8]) - 1
        # 在list中寻找对应的dict
        ifFind = False
        for j in range(len(data)):
            if data[j]['index'] == sun_index:
                data[j]['sum_data'][count, :] = image_data[config.sum_row_index, :]
                ifFind = True
                break
        if not ifFind:
            data.append({
                'index': sun_index,
                'sum_data': np.zeros((config.sun_row_count, standard_img.shape[1]), dtype=np.int16)
            })
            data[len(data) - 1]['sum_data'][count, :] = image_data[config.sum_row_index, :]
        image_file.close()
    # 去除负值
    for d in data:
        d['sum_data'][d['sum_data'] < 0] = 0
    if config.save_img_form == 'default':
        # 使用读取的色谱进行输出 imsave函数将自动对data进行归一化
        for d in data:
            print('输出序号为' + str(d['index']) + '的png...')
            plt.imsave(sum_file_path + 'sum' + str(d['index']) + ".png", d['sum_data'], cmap=color_map)
    if config.save_img_form == 'fts':
        # 不对data进行任何操作 直接输出为fts文件
        for d in data:
            print('输出序号为' + str(d['index']) + '的fits...')
            primaryHDU = fits.PrimaryHDU(d['sum_data'])
            greyHDU = fits.HDUList([primaryHDU])
            greyHDU.writeto(sum_file_path + 'sum' + str(d['index']) + '.fts')
    # 程序结束
    time_end = time.time()
    print('所有进度已完成，所花费总时间为：', (time_end - time_start) / 60, 'min(分钟)')
    print('程序结束！')


if __name__ == "__main__":
    main()
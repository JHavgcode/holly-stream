#!/bin/bash

# Installation script for holly-stream
# This script assumes you are using a fresh install of Jetpack 4.6
# If there are dependency issues, this script will create virtual environments when possible

# Allocate more swap memory for your Jetson Nano
fallocate -l 4G /var/swapfile 
chmod 600 /var/swapfile
mkswap /var/swapfile
swapon /var/swapfile
bash -c "echo '/var/swapfile swap swap defaults 0 0'  >> /etc/fstab"

# create directory where all building and source code will be
mkdir build
export BUILD_DIR=${PWD}/build
cd ${BUILD_DIR}

# OpenCV (4.5.1) installation from source
# The default OpenCV version on the Jetson Nano (Jetpack 4.6.1) is 4.1.1
# The default version 4.1.1 causes dependency issues

bash -c "echo '/usr/local/cuda/lib64' >> /etc/ld.so.conf.d/nvidia-tegra.conf"
ldconfig
apt-get update
apt-get install -y \
git cmake curl unzip pkg-config libpng-dev libtiff-dev \
libavcodec-dev libavformat-dev libswscale-dev \
libgtk2.0-dev libcanberra-gtk* \
python3-dev python3-numpy python3-pip \ 
libxvidcore-dev libx264-dev libgtk-3-dev \
libtbb2 libtbb-dev libdc1394-22-dev \
libv4l-dev v4l-utils \
libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
libavresample-dev libvorbis-dev libxine2-dev \
libfaac-dev libmp3lame-dev libtheora-dev \
libopencore-amrnb-dev libopencore-amrwb-dev \
libopenblas-dev libatlas-base-dev libblas-dev libopenblas-base \
liblapack-dev libeigen3-dev gfortran \
libhdf5-dev protobuf-compiler \
libprotobuf-dev libgoogle-glog-dev libgflags-dev \
libopenmpi-dev libomp-dev ffmpeg

pip3 install virtualenv
python3 -m virtualenv -p python3 holly-stream-env --system-site-packages
source ./holly-stream-env/bin/activate

export OPENCV_VERSION=4.5.1

wget -O opencv.zip https://github.com/opencv/opencv/archive/${OPENCV_VERSION}.zip
wget -O opencv_contrib.zip https://github.com/opencv/opencv_contrib/archive/${OPENCV_VERSION}.zip
unzip opencv.zip
unzip opencv_contrib.zip
mv opencv-${OPENCV_VERSION} opencv
mv opencv_contrib-${OPENCV_VERSION} opencv_contrib
rm opencv.zip
rm opencv_contrib.zip
mkdir -p ./opencv/build
cd ./opencv/build

cmake -D CMAKE_BUILD_TYPE=RELEASE -D CMAKE_INSTALL_PREFIX=/usr \
-D OPENCV_EXTRA_MODULES_PATH=${BUILD_DIR}/opencv_contrib/modules \
-D EIGEN_INCLUDE_PATH=/usr/include/eigen3 -D WITH_OPENCL=OFF \
-D WITH_CUDA=ON -D CUDA_ARCH_BIN=5.3 -D CUDA_ARCH_PTX="" \
-D WITH_CUDNN=ON -D WITH_CUBLAS=ON -D ENABLE_FAST_MATH=ON \
-D CUDA_FAST_MATH=ON -D OPENCV_DNN_CUDA=ON -D ENABLE_NEON=ON \
-D WITH_QT=OFF -D WITH_OPENMP=ON -D WITH_OPENGL=ON -D BUILD_TIFF=ON \
-D WITH_FFMPEG=ON -D WITH_GSTREAMER=ON -D WITH_TBB=ON -D BUILD_TBB=ON \
-D BUILD_TESTS=OFF -D WITH_EIGEN=ON -D WITH_V4L=ON -D WITH_LIBV4L=ON \
-D OPENCV_ENABLE_NONFREE=ON -D INSTALL_C_EXAMPLES=OFF \
-D INSTALL_PYTHON_EXAMPLES=OFF -D BUILD_NEW_PYTHON_SUPPORT=ON \
-D BUILD_opencv_python3=TRUE -D OPENCV_GENERATE_PKGCONFIG=ON \
-D BUILD_EXAMPLES=OFF ..
make -j4

make install
ldconfig
make clean
apt-get update
cd ${BUILD_DIR}

# PyTorch (1.8.0) and Torchvision (0.9.0) installation via pip wheel
export PYTORCH_VERSION=1.8.0
export TORCHVISION_VERSION=0.9.0
export TORCHVISION_BRANCH=release/0.9

wget https://nvidia.box.com/shared/static/p57jwntv436lfrd78inwl7iml6p13fzh.whl \
-O torch-${PYTORCH_VERSION}-cp36-cp36m-linux_aarch64.whl
pip3 install Cython
pip3 install numpy torch-${PYTORCH_VERSION}-cp36-cp36m-linux_aarch64.whl

apt-get install -y libjpeg-dev zlib1g-dev libpython3-dev libfreetype6-dev
git clone --branch ${TORCHVISION_BRANCH} https://github.com/pytorch/vision torchvision
cd torchvision
export BUILD_VERSION=${TORCHVISION_VERSION}
python3 setup.py install --user
cd ..

apt-get install -y --no-install-recommends \
software-properties-common \
autoconf \
automake \
build-essential \
libb64-dev \
libre2-dev \
libssl-dev \
libtool \
libboost-dev \
libcurl4-openssl-dev \
rapidjson-dev \
patchelf

wget https://github.com/triton-inference-server/server/releases/download/v2.16.0/tritonserver2.16.0-jetpack4.6.tgz
mkdir tritonserver
tar -xzf tritonserver2.16.0-jetpack4.6.tgz -C ${BUILD_DIR}/tritonserver/

export BACKEND_PATH=${BUILD_DIR}/tritonserver/backends

pip3 install --upgrade pip
pip3 install --upgrade grpcio-tools numpy==1.19.4 future attrdict nanocamera docker-compose==1.27.4
pip3 install --upgrade ${BUILDDIR}/tritonserver/clients/python/tritonclient-2.16.0-py3-none-any.whl[all]
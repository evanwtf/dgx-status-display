# ESP-IDF project for the Waveshare ESP32-C6-Touch-AMOLED-2.16 status display.
cmake_minimum_required(VERSION 3.16)
# The firmware version. The board sends it in its hello line.
set(PROJECT_VER "0.1.0")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(dgx_status_display)

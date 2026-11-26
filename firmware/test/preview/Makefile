# Host preview of the firmware UI. Builds LVGL, the fonts, view.c and the
# pure logic with the host compiler, using the firmware's own LVGL settings
# (every CONFIG_LV_* value from the IDF build's sdkconfig.h). Run it in the
# pinned IDF image after `idf.py reconfigure` or a build:
#
#   scripts/firmware.py preview      # PNGs in firmware/preview/

FW := ../..
IDF_PATH ?= /opt/esp/idf
LVGL_DIR := $(FW)/managed_components/lvgl__lvgl
SDKCONFIG_H := $(FW)/build/config/sdkconfig.h
CJSON_DIR ?= $(IDF_PATH)/components/json/cJSON
BUILD ?= build
OUT ?= $(FW)/preview

LVGL_SRC := $(shell find $(LVGL_DIR)/src -name '*.c' | sort)
LVGL_OBJ := $(patsubst $(LVGL_DIR)/%.c,$(BUILD)/lvgl/%.o,$(LVGL_SRC))

APP_SRC := preview.c png.c $(FW)/main/view.c \
	$(wildcard $(FW)/main/fonts/*.c) \
	$(FW)/main/logic/tiles.c $(FW)/main/logic/carousel.c $(FW)/main/logic/fmt.c \
	$(FW)/main/logic/link_state.c $(FW)/main/logic/frame_parse.c \
	$(FW)/main/logic/palette.c

KCONFIG := $(BUILD)/lv_kconfig.h
COMMON := -O1 -include $(KCONFIG) -I$(LVGL_DIR) -I$(FW)/main
# Same warning set as the firmware's own code (main/CMakeLists.txt).
APP_CFLAGS := $(COMMON) -std=gnu11 -Wall -Wextra -Werror -Wshadow -I$(FW)/main/logic \
	-I$(CJSON_DIR) -I.

.PHONY: preview clean
preview: $(BUILD)/preview
	mkdir -p $(OUT)
	./$(BUILD)/preview $(OUT)

$(KCONFIG): $(SDKCONFIG_H)
	mkdir -p $(BUILD)
	grep '^#define CONFIG_LV_' $< > $@

$(BUILD)/lvgl/%.o: $(LVGL_DIR)/%.c $(KCONFIG)
	@mkdir -p $(dir $@)
	@$(CC) $(COMMON) -w -c $< -o $@

$(BUILD)/cJSON.o: $(CJSON_DIR)/cJSON.c
	mkdir -p $(BUILD)
	$(CC) -O1 -DCJSON_NESTING_LIMIT=8 -c $< -o $@

$(BUILD)/preview: $(APP_SRC) $(LVGL_OBJ) $(BUILD)/cJSON.o $(KCONFIG) Makefile
	@echo "CC preview (+ $(words $(LVGL_OBJ)) LVGL objects)"
	@$(CC) $(APP_CFLAGS) $(APP_SRC) $(LVGL_OBJ) $(BUILD)/cJSON.o -lm -o $@

clean:
	rm -rf $(BUILD)

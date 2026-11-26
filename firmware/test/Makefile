# Host unit tests for the pure firmware logic (main/logic/).
#
# Builds with the host compiler, under AddressSanitizer and UBSan. cJSON comes
# from the ESP-IDF tree, the same source the firmware links. Run it inside the
# pinned espressif/idf image (see README), where IDF_PATH is set:
#
#   make -C firmware/test
#
# Elsewhere, point CJSON_DIR at a cJSON checkout.

IDF_PATH ?= /opt/esp/idf
CJSON_DIR ?= $(IDF_PATH)/components/json/cJSON
BUILD ?= build

CFLAGS := -std=c11 -Wall -Wextra -Wpedantic -Werror -Wshadow -Wconversion \
	-Wno-sign-conversion -O1 -g -fno-omit-frame-pointer \
	-fsanitize=address,undefined -fno-sanitize-recover=all \
	-I../main/logic -I$(CJSON_DIR)
# cJSON is third-party code: build it without our warning set.
# Same nesting limit as the firmware build (firmware/CMakeLists.txt).
CJSON_CFLAGS := -std=c99 -O1 -g -fsanitize=address,undefined -I$(CJSON_DIR) \
	-DCJSON_NESTING_LIMIT=8

LOGIC := ../main/logic/frame_parse.c ../main/logic/line_reader.c \
	../main/logic/link_state.c ../main/logic/fmt.c ../main/logic/burnin.c \
	../main/logic/tiles.c ../main/logic/carousel.c ../main/logic/report.c ../main/logic/palette.c ../main/logic/history.c ../main/logic/settings.c

.PHONY: test clean
test: $(BUILD)/test_logic
	./$(BUILD)/test_logic

$(BUILD)/cJSON.o: $(CJSON_DIR)/cJSON.c Makefile
	mkdir -p $(BUILD)
	$(CC) $(CJSON_CFLAGS) -c $< -o $@

$(BUILD)/test_logic: test_logic.c $(LOGIC) $(BUILD)/cJSON.o Makefile
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) test_logic.c $(LOGIC) $(BUILD)/cJSON.o -lm -o $@

clean:
	rm -rf $(BUILD)

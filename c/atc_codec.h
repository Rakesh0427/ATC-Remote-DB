#ifndef ATC_CODEC_H
#define ATC_CODEC_H

#include <stddef.h>
#include <stdint.h>

#define ATC_TEXT_MAX 220
#define ATC_BYTES_PER_SLICE 94
#define ATC_BLOCKS_PER_MESSAGE 16
#define ATC_HEADER_LEN 0x28
#define ATC_RECORD_LEN 55

typedef struct {
    int index;
    char gfd[8];
    char name[16];
    char country[4];
    char tsel[8];
} AtcCentre;

typedef struct {
    int recognized;
    char db_id[12];
    char part[20];
    int slots;
    int centre_count;
    int empty_slots;
    AtcCentre centres[160];
} AtcDbInfo;

typedef struct {
    char *text;
    size_t text_len;
    char session[5];
    char crc32_hex[9];
    size_t size;
    int data_slices;
    int air_blocks;
    int messages;
    int max_text;
} AtcVhf;

typedef struct {
    uint8_t *data;
    size_t len;
    char crc32_hex[9];
} AtcImage;

uint32_t atc_crc32(const uint8_t *data, size_t len);
uint16_t atc_crc16(const uint8_t *data, size_t len);
void atc_crc32_hex(const uint8_t *data, size_t len, char out[9]);

int atc_read_file(const char *path, uint8_t **data, size_t *len, char *err, size_t err_len);
int atc_write_text(const char *path, const char *text, char *err, size_t err_len);
int atc_read_text(const char *path, char **text, size_t *len, char *err, size_t err_len);

void atc_inspect(const uint8_t *data, size_t len, AtcDbInfo *info);

/* Encode the raw image into the injector .vhf script used on VHF. */
int atc_encode_vhf(const uint8_t *data, size_t len, const char *label, const char *address,
                   const char *ack, int delay_ms, AtcVhf *out, char *err, size_t err_len);
void atc_vhf_free(AtcVhf *vhf);

/* Decode an injector .vhf script back to the image. Checks every block CRC and the image CRC. */
int atc_decode_vhf(const char *vhf, AtcImage *out, char *err, size_t err_len);
void atc_image_free(AtcImage *image);

#endif

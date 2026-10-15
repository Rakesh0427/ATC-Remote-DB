/*
 * Menu simulation: read an ATC DB bin, encode it to an ACARS .vhf injector
 * file, decode that file, and compare size, CRC, bytes and centre fields.
 *
 *   gcc -Wall -Wextra -std=c11 -o c/test_sim c/atc_codec.c c/test_sim.c
 *   ./c/test_sim
 *   ./c/test_sim --auto samples/SGC10001.bin
 */

#include "atc_codec.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ERR_LEN 160

typedef struct {
    char bin_path[512];
    char vhf_path[512];
    char label[3];
    char address[8];
    char ack[4];
    int delay_ms;
    uint8_t *original;
    size_t original_len;
    AtcImage decoded;
    int has_decoded;
} Sim;

static void sim_init(Sim *sim) {
    memset(sim, 0, sizeof(*sim));
    snprintf(sim->bin_path, sizeof(sim->bin_path), "samples/SGC10001.bin");
    snprintf(sim->vhf_path, sizeof(sim->vhf_path), "c/SGC10001.vhf");
    snprintf(sim->label, sizeof(sim->label), "C1");
    snprintf(sim->address, sizeof(sim->address), "#ID#");
    snprintf(sim->ack, sizeof(sim->ack), "ACK");
    sim->delay_ms = 1000;
}

static void sim_free(Sim *sim) {
    free(sim->original);
    sim->original = NULL;
    atc_image_free(&sim->decoded);
    sim->has_decoded = 0;
}

static int file_exists(const char *path) {
    FILE *fp = fopen(path, "rb");
    if (!fp) {
        return 0;
    }
    fclose(fp);
    return 1;
}

static void choose_default_bin(Sim *sim) {
    if (file_exists(sim->bin_path)) {
        return;
    }
    if (file_exists("../samples/SGC10001.bin")) {
        snprintf(sim->bin_path, sizeof(sim->bin_path), "../samples/SGC10001.bin");
        snprintf(sim->vhf_path, sizeof(sim->vhf_path), "SGC10001.vhf");
    }
}

static void print_rule(void) {
    printf("------------------------------------------------\n");
}

static void print_status(const Sim *sim) {
    char crc[9] = "--------";
    if (sim->original) {
        atc_crc32_hex(sim->original, sim->original_len, crc);
    }
    printf(" bin : %s  %s\n", sim->bin_path, sim->original ? "loaded" : "not loaded");
    if (sim->original) {
        printf("       %zu bytes  CRC32 %s\n", sim->original_len, crc);
    }
    printf(" vhf : %s  %s\n", sim->vhf_path, file_exists(sim->vhf_path) ? "on disk" : "not written");
    printf(" wrap: label %s  address %s  <%s>  DELAY=%d\n", sim->label, sim->address, sim->ack, sim->delay_ms);
    print_rule();
}

static void print_centres(const AtcDbInfo *info, int limit) {
    int n = info->centre_count < limit ? info->centre_count : limit;
    printf("  idx  GFD   name          ctry  tsel\n");
    for (int i = 0; i < n; i++) {
        const AtcCentre *c = &info->centres[i];
        printf("  %3d  %-4s  %-12s  %-4s  %s\n", c->index, c->gfd, c->name, c->country, c->tsel);
    }
    if (info->centre_count > n) {
        printf("  ... %d centres total\n", info->centre_count);
    }
}

static int do_read(Sim *sim) {
    free(sim->original);
    sim->original = NULL;
    sim->original_len = 0;
    char err[ERR_LEN];
    if (atc_read_file(sim->bin_path, &sim->original, &sim->original_len, err, sizeof(err)) != 0) {
        printf("READ FAIL  %s\n", err);
        return -1;
    }
    char crc[9];
    atc_crc32_hex(sim->original, sim->original_len, crc);
    printf("READ PASS  %zu bytes  CRC32 %s\n", sim->original_len, crc);
    AtcDbInfo info;
    atc_inspect(sim->original, sim->original_len, &info);
    if (info.recognized) {
        printf("       part %s  id %s  centres %d  empty slots %d\n", info.part, info.db_id, info.centre_count,
               info.empty_slots);
    } else {
        printf("       layout is not the 55-byte centre database; raw image still encodes\n");
    }
    return 0;
}

static int do_show_db(Sim *sim) {
    if (!sim->original) {
        printf("Load the database first.\n");
        return -1;
    }
    AtcDbInfo info;
    atc_inspect(sim->original, sim->original_len, &info);
    char crc[9];
    atc_crc32_hex(sim->original, sim->original_len, crc);
    printf("DATABASE\n");
    printf("  size   %zu\n", sim->original_len);
    printf("  crc32  %s\n", crc);
    printf("  head   ");
    for (size_t i = 0; i < 16 && i < sim->original_len; i++) {
        printf("%02X ", sim->original[i]);
    }
    printf("\n");
    if (!info.recognized) {
        printf("  not a recognized centre table\n");
        return 0;
    }
    printf("  part   %s\n", info.part);
    printf("  id     %s\n", info.db_id);
    printf("  slots  %d used %d empty %d\n", info.slots, info.centre_count, info.empty_slots);
    print_centres(&info, 8);
    return 0;
}

static void print_soh_lines(const char *text, int head, int tail) {
    const char *p = text;
    int index = 0;
    int total = 0;
    for (const char *q = text; (q = strstr(q, "<SOH>")) != NULL; q++) {
        total++;
    }
    while ((p = strstr(p, "<SOH>")) != NULL) {
        const char *nl = strchr(p, '\n');
        int show = index < head || index >= total - tail;
        if (show) {
            if (index == head && total > head + tail) {
                printf("  ...\n");
            }
            printf("  ");
            size_t n = nl ? (size_t)(nl - p) : strlen(p);
            if (n && p[n - 1] == '\r') {
                n--;
            }
            fwrite(p, 1, n, stdout);
            printf("\n");
        }
        index++;
        p++;
    }
}

static int do_encode(Sim *sim) {
    if (!sim->original) {
        printf("Load the database first.\n");
        return -1;
    }
    AtcVhf vhf;
    char err[ERR_LEN];
    if (atc_encode_vhf(sim->original, sim->original_len, sim->label, sim->address, sim->ack, sim->delay_ms, &vhf,
                       err, sizeof(err)) != 0) {
        printf("ENCODE FAIL  %s\n", err);
        return -1;
    }
    if (atc_write_text(sim->vhf_path, vhf.text, err, sizeof(err)) != 0) {
        printf("ENCODE FAIL  %s\n", err);
        atc_vhf_free(&vhf);
        return -1;
    }
    printf("ENCODE PASS  wrote %s\n", sim->vhf_path);
    printf("  session %s  image CRC32 %s\n", vhf.session, vhf.crc32_hex);
    printf("  data slices %d  air blocks %d  messages %d  longest text %d/%d\n", vhf.data_slices, vhf.air_blocks,
           vhf.messages, vhf.max_text, ATC_TEXT_MAX);
    printf("  format <SOH> ? %s <%s> %s <block id> <STX> hex <ETB|ETX> + DELAY\n", sim->address, sim->ack, sim->label);
    print_soh_lines(vhf.text, 2, 1);
    atc_vhf_free(&vhf);
    return 0;
}

static int do_show_vhf(Sim *sim) {
    char *text = NULL;
    size_t len = 0;
    char err[ERR_LEN];
    if (atc_read_text(sim->vhf_path, &text, &len, err, sizeof(err)) != 0) {
        printf("SHOW VHF FAIL  %s\n", err);
        return -1;
    }
    int soh = 0;
    int etb = 0;
    int etx = 0;
    int delay = 0;
    int max_text = 0;
    const char *p = text;
    while ((p = strstr(p, "<SOH>")) != NULL) {
        soh++;
        const char *stx = strstr(p, "<STX>");
        const char *et = strstr(p, "<ET");
        if (stx && et && et > stx) {
            int n = (int)(et - (stx + 5));
            if (n > max_text) {
                max_text = n;
            }
        }
        p++;
    }
    for (const char *q = text; (q = strstr(q, "<ETB>")) != NULL; q++) {
        etb++;
    }
    for (const char *q = text; (q = strstr(q, "<ETX>")) != NULL; q++) {
        etx++;
    }
    for (const char *q = text; (q = strstr(q, "DELAY=")) != NULL; q++) {
        delay++;
    }
    printf("VHF FILE  %s  %zu bytes\n", sim->vhf_path, len);
    printf("  uplinks %d  <ETB> %d  <ETX> %d  DELAY lines %d  longest text %d\n", soh, etb, etx, delay, max_text);
    print_soh_lines(text, 2, 1);
    free(text);
    return 0;
}

static int do_decode(Sim *sim) {
    char *text = NULL;
    size_t len = 0;
    char err[ERR_LEN];
    if (atc_read_text(sim->vhf_path, &text, &len, err, sizeof(err)) != 0) {
        printf("DECODE FAIL  %s\n", err);
        return -1;
    }
    atc_image_free(&sim->decoded);
    sim->has_decoded = 0;
    if (atc_decode_vhf(text, &sim->decoded, err, sizeof(err)) != 0) {
        printf("DECODE FAIL  %s\n", err);
        free(text);
        return -1;
    }
    free(text);
    sim->has_decoded = 1;
    printf("DECODE PASS  %zu bytes  CRC32 %s\n", sim->decoded.len, sim->decoded.crc32_hex);
    printf("  rebuilt from %s, not from the original buffer\n", sim->vhf_path);
    return 0;
}

static const AtcCentre *find_centre(const AtcDbInfo *info, const char *gfd) {
    for (int i = 0; i < info->centre_count; i++) {
        if (strcmp(info->centres[i].gfd, gfd) == 0) {
            return &info->centres[i];
        }
    }
    return NULL;
}

static void print_check(const char *name, int pass, const char *detail) {
    printf("  %-10s %s  %s\n", name, pass ? "PASS" : "FAIL", detail);
}

static int do_compare(Sim *sim) {
    if (!sim->original || !sim->has_decoded) {
        printf("Need both a loaded database and a decoded image.\n");
        return -1;
    }
    char a_crc[9];
    char b_crc[9];
    atc_crc32_hex(sim->original, sim->original_len, a_crc);
    atc_crc32_hex(sim->decoded.data, sim->decoded.len, b_crc);
    int size_ok = sim->original_len == sim->decoded.len;
    int crc_ok = strcmp(a_crc, b_crc) == 0 && strcmp(a_crc, sim->decoded.crc32_hex) == 0;
    size_t cmp_len = sim->original_len < sim->decoded.len ? sim->original_len : sim->decoded.len;
    int bytes_ok = size_ok && memcmp(sim->original, sim->decoded.data, cmp_len) == 0;
    size_t mismatch = 0;
    if (!bytes_ok) {
        size_t n = cmp_len;
        for (size_t i = 0; i < n; i++) {
            if (sim->original[i] != sim->decoded.data[i]) {
                mismatch = i;
                break;
            }
        }
    }
    AtcDbInfo a, b;
    atc_inspect(sim->original, sim->original_len, &a);
    atc_inspect(sim->decoded.data, sim->decoded.len, &b);
    int part_ok = a.recognized && b.recognized && strcmp(a.part, b.part) == 0;
    int id_ok = a.recognized && b.recognized && strcmp(a.db_id, b.db_id) == 0;
    int count_ok = a.centre_count == b.centre_count && a.empty_slots == b.empty_slots;
    const AtcCentre *ea = find_centre(&a, "EDYY");
    const AtcCentre *eb = find_centre(&b, "EDYY");
    int edyy_ok = ea && eb && strcmp(ea->name, eb->name) == 0 && strcmp(ea->country, eb->country) == 0 &&
                  strcmp(ea->tsel, eb->tsel) == 0;
    int trailer_ok = 0;
    if (sim->original_len >= 24 && sim->decoded.len >= 24) {
        trailer_ok = memcmp(sim->original + sim->original_len - 24, sim->decoded.data + sim->decoded.len - 24, 24) == 0;
    }

    printf("COMPARE  original vs decoded .vhf\n");
    char detail[80];
    snprintf(detail, sizeof(detail), "original %zu  decoded %zu", sim->original_len, sim->decoded.len);
    print_check("size", size_ok, detail);
    snprintf(detail, sizeof(detail), "%s  %s", a_crc, b_crc);
    print_check("crc32", crc_ok, detail);
    if (bytes_ok) {
        snprintf(detail, sizeof(detail), "%zu/%zu bytes equal", sim->original_len, sim->original_len);
    } else {
        snprintf(detail, sizeof(detail), "first mismatch at offset %zu", mismatch);
    }
    print_check("data", bytes_ok, detail);
    if (a.recognized) {
        snprintf(detail, sizeof(detail), "%s / %s", a.part, b.part);
        print_check("part", part_ok, detail);
        snprintf(detail, sizeof(detail), "%s / %s", a.db_id, b.db_id);
        print_check("db id", id_ok, detail);
        snprintf(detail, sizeof(detail), "%d centres, %d empty / %d centres, %d empty", a.centre_count, a.empty_slots,
                 b.centre_count, b.empty_slots);
        print_check("centres", count_ok, detail);
        snprintf(detail, sizeof(detail), "%s %s %s / %s %s %s", ea ? ea->name : "-", ea ? ea->country : "-",
                 ea ? ea->tsel : "-", eb ? eb->name : "-", eb ? eb->country : "-", eb ? eb->tsel : "-");
        print_check("EDYY", edyy_ok, detail);
        print_check("trailer", trailer_ok, "last 24 bytes");
    }
    printf("  original ");
    for (size_t i = 0; i < 8 && i < sim->original_len; i++) {
        printf("%02X", sim->original[i]);
    }
    printf("\n  decoded  ");
    for (size_t i = 0; i < 8 && i < sim->decoded.len; i++) {
        printf("%02X", sim->decoded.data[i]);
    }
    printf("\n");
    int pass = size_ok && crc_ok && bytes_ok && (!a.recognized || (part_ok && id_ok && count_ok && edyy_ok && trailer_ok));
    printf("RESULT %s\n", pass ? "PASS" : "FAIL");
    return pass ? 0 : -1;
}

static int expect_decode_fail(const char *vhf, const char *what) {
    AtcImage image;
    char err[ERR_LEN];
    int rc = atc_decode_vhf(vhf, &image, err, sizeof(err));
    if (rc == 0) {
        atc_image_free(&image);
        printf("TAMPER FAIL  %s was accepted\n", what);
        return -1;
    }
    printf("TAMPER PASS  %s rejected: %s\n", what, err);
    return 0;
}

static int do_tamper(Sim *sim) {
    char *text = NULL;
    size_t len = 0;
    char err[ERR_LEN];
    if (atc_read_text(sim->vhf_path, &text, &len, err, sizeof(err)) != 0) {
        printf("TAMPER FAIL  %s\n", err);
        return -1;
    }
    char *stx = strstr(text, "<STX>");
    char *crc_hit = NULL;
    if (stx) {
        char *et = strstr(stx, "<ET");
        if (et && et - stx > 6 && et[-5] == '/') {
            crc_hit = et - 5;
        }
    }
    char *hex_hit = strstr(text, "/F/0000/");
    int ok = 0;
    if (crc_hit && crc_hit[1] && crc_hit[2]) {
        char saved = crc_hit[1];
        crc_hit[1] = (saved == '0') ? '1' : '0';
        ok = expect_decode_fail(text, "flipped block CRC") == 0;
        crc_hit[1] = saved;
    } else {
        printf("TAMPER FAIL  could not find a CRC field\n");
        ok = 0;
    }
    if (ok && hex_hit) {
        char *digit = hex_hit + 8;
        char saved = *digit;
        *digit = (saved == 'A') ? 'B' : 'A';
        ok = expect_decode_fail(text, "flipped payload nibble") == 0;
        *digit = saved;
    }
    free(text);
    return ok ? 0 : -1;
}

static int do_full(Sim *sim) {
    printf("\n=== full simulation ===\n");
    if (do_read(sim) != 0 || do_show_db(sim) != 0 || do_encode(sim) != 0 || do_show_vhf(sim) != 0 || do_decode(sim) != 0 ||
        do_compare(sim) != 0 || do_tamper(sim) != 0) {
        printf("SIMULATION FAIL\n");
        return -1;
    }
    if (sim->original_len == 8204) {
        char crc[9];
        atc_crc32_hex(sim->original, sim->original_len, crc);
        if (strcmp(crc, "DE4D3B1B") == 0) {
            printf("ORACLE PASS  CRC32 matches the ground tool for SGC10001\n");
        }
    }
    printf("SIMULATION PASS\n");
    return 0;
}

static void print_menu(const Sim *sim) {
    printf("\n================================================\n");
    printf(" ATC database  ->  ACARS VHF  simulation\n");
    printf("================================================\n");
    print_status(sim);
    printf(" 1  Read ATC DB bin\n");
    printf(" 2  Show database\n");
    printf(" 3  Encode bin to ACARS .vhf\n");
    printf(" 4  Show .vhf uplink\n");
    printf(" 5  Decode .vhf\n");
    printf(" 6  Compare with original\n");
    printf(" 7  Run full simulation\n");
    printf(" 8  Settings\n");
    printf(" 0  Exit\n");
    printf("select: ");
}

static void read_line(char *dst, size_t n) {
    if (!fgets(dst, (int)n, stdin)) {
        dst[0] = '0';
        dst[1] = 0;
        return;
    }
    dst[strcspn(dst, "\r\n")] = 0;
}

static void do_settings(Sim *sim) {
    char line[512];
    printf("settings  a label  b address  c ack  d delay  e bin path  f vhf path  0 back\n");
    printf("select: ");
    read_line(line, sizeof(line));
    if (line[0] == 'a') {
        printf("label (2 chars): ");
        read_line(line, sizeof(line));
        if (strlen(line) == 2) {
            sim->label[0] = line[0];
            sim->label[1] = line[1];
            sim->label[2] = 0;
        }
    } else if (line[0] == 'b') {
        printf("address (#ID# or tail): ");
        read_line(line, sizeof(line));
        if (line[0] && strlen(line) < sizeof(sim->address)) {
            memcpy(sim->address, line, strlen(line) + 1);
        }
    } else if (line[0] == 'c') {
        snprintf(sim->ack, sizeof(sim->ack), "%s", strcmp(sim->ack, "ACK") == 0 ? "NAK" : "ACK");
        printf("ack is now %s\n", sim->ack);
    } else if (line[0] == 'd') {
        printf("delay ms: ");
        read_line(line, sizeof(line));
        int d = atoi(line);
        if (d >= 0 && d <= 120000) {
            sim->delay_ms = d;
        }
    } else if (line[0] == 'e') {
        printf("bin path: ");
        read_line(line, sizeof(line));
        if (line[0]) {
            snprintf(sim->bin_path, sizeof(sim->bin_path), "%s", line);
        }
    } else if (line[0] == 'f') {
        printf("vhf path: ");
        read_line(line, sizeof(line));
        if (line[0]) {
            snprintf(sim->vhf_path, sizeof(sim->vhf_path), "%s", line);
        }
    }
}

static int run_menu(Sim *sim) {
    char line[32];
    for (;;) {
        print_menu(sim);
        read_line(line, sizeof(line));
        if (line[0] == '0' && line[1] == 0) {
            return 0;
        }
        if (strcmp(line, "1") == 0) {
            do_read(sim);
        } else if (strcmp(line, "2") == 0) {
            do_show_db(sim);
        } else if (strcmp(line, "3") == 0) {
            do_encode(sim);
        } else if (strcmp(line, "4") == 0) {
            do_show_vhf(sim);
        } else if (strcmp(line, "5") == 0) {
            do_decode(sim);
        } else if (strcmp(line, "6") == 0) {
            do_compare(sim);
        } else if (strcmp(line, "7") == 0) {
            do_full(sim);
        } else if (strcmp(line, "8") == 0) {
            do_settings(sim);
        } else {
            printf("unknown selection\n");
        }
    }
}

int main(int argc, char **argv) {
    Sim sim;
    sim_init(&sim);
    choose_default_bin(&sim);
    int auto_mode = 0;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--auto") == 0) {
            auto_mode = 1;
        } else if (argv[i][0] != '-') {
            snprintf(sim.bin_path, sizeof(sim.bin_path), "%s", argv[i]);
        }
    }
    int rc = 0;
    if (auto_mode) {
        rc = do_full(&sim);
    } else {
        rc = run_menu(&sim);
    }
    sim_free(&sim);
    return rc == 0 ? 0 : 1;
}

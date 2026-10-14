/*
 * flash_layout.h -- where things live in the cartridge's QSPI flash.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * THE SINGLE SOURCE OF TRUTH for flash addresses.
 * tools/flash_layout_check.py parses the #define lines below and fails the build
 * if the firmware ELF's flash footprint crosses FLASH_WHX_ADDR, or if the WHX
 * would cross FLASH_ASSETS_ADDR.  Sizes assume the cartridge's 4 MB flash; the
 * firmware compares flash_get_size() against FLASH_TOTAL_BYTES at boot and
 * disables saving on mismatch.
 */
#ifndef FCPICO_FLASH_LAYOUT_H
#define FCPICO_FLASH_LAYOUT_H

#define FLASH_XIP_BASE        0x10000000u
#define FLASH_TOTAL_BYTES     0x00400000u   // 4 MB

#define FLASH_FIRMWARE_ADDR   0x10000000u   // code, boot ROM image, music, SFX
#define FLASH_FIRMWARE_MAX    0x00080000u   // 512 KB cap
#define FLASH_WHX_ADDR        0x10080000u   // doom1.whx (TINY_WAD_ADDR for the fcpico target)
#define FLASH_WHX_MAX         0x00200000u   // 2 MB reserved (shareware doom1.whx is 1,800,344 B)
#define FLASH_WORLD_ADDR      0x10280000u   // optional world sprite atlas, after the WHX
#define FLASH_WORLD_MAX       0x00080000u   // 512 KB, ends at FLASH_ASSETS_ADDR
#define FLASH_ASSETS_ADDR     0x10300000u   // optional packed asset archive
#define FLASH_ASSETS_MAX      0x00080000u   // 512 KB
#define FLASH_AUDIO_MAX       0x00080000u   // generated audio archive: the whole asset region
#define FLASH_SAVES_ADDR      0x10380000u   // RP2040 Doom save slots (from the top of flash down)
#define FLASH_SAVES_MAX       0x00080000u   // 512 KB (7 slots x 64 KB + config)
#define FLASH_CONFIG_ADDR     0x103FF000u   // last 4 KB sector: fcpico options

#endif /* FCPICO_FLASH_LAYOUT_H */

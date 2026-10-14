-- SPDX-License-Identifier: BSD-3-Clause
-- Per-frame evidence for the v5 world sprite runtime: screenshot, OAM, sprite
-- CHR and palettes for every PPU frame in [FCPICO_CAPTURE_FROM, FCPICO_FRAMES],
-- plus NMI exit scanlines over the whole capture window.
local output = assert(os.getenv("FCPICO_RESULTS"))
local limit = assert(tonumber(os.getenv("FCPICO_FRAMES")))
local first = assert(tonumber(os.getenv("FCPICO_CAPTURE_FROM")))
local nmi_rti = os.getenv("FCPICO_NMI_RTI")
local frames = 0
local nmi_count, nmi_min, nmi_max = 0, 1000, -1

if nmi_rti then
    emu.addMemoryCallback(function()
        if frames >= first then
            local scanline = emu.getState()["ppu.scanline"]
            nmi_count = nmi_count + 1
            nmi_min = math.min(nmi_min, scanline)
            nmi_max = math.max(nmi_max, scanline)
        end
    end, emu.callbackType.exec, tonumber(nmi_rti, 16))
end

local function read(kind, start, count)
    local bytes = {}
    for address = start, start + count - 1 do
        bytes[#bytes + 1] = string.char(emu.read(address, kind))
    end
    return table.concat(bytes)
end

-- OAM, CHR and palettes are sampled when the frame starts rendering, so they
-- describe the same picture as the screenshot taken at its end.
local state = nil
emu.addEventCallback(function()
    if frames + 1 >= first then
        state = {
            oam = read(emu.memType.nesSpriteRam, 0, 256),
            chr = read(emu.memType.nesPpuDebug, 0x1000, 4096),
            pal = read(emu.memType.nesPpuDebug, 0x3f00, 32),
            attr = read(emu.memType.nesPpuDebug, 0x23c0, 64),
        }
    end
end, emu.eventType.startFrame)

local function save(name, data)
    local file = assert(io.open(output .. "/" .. name, "wb"))
    file:write(data)
    file:close()
end

emu.addEventCallback(function()
    frames = frames + 1
    if frames >= first and state then
        local stem = string.format("f%05d", frames)
        save(stem .. ".png", emu.takeScreenshot())
        save(stem .. ".oam", state.oam)
        save(stem .. ".chr", state.chr)
        save(stem .. ".pal", state.pal)
        save(stem .. ".attr", state.attr)
    end
    if frames == limit then
        local result = assert(io.open(output .. "/lua_result.json", "w"))
        result:write(string.format('{"frames":%d,"nmi_count":%d,"nmi_min_scanline":%d,"nmi_max_scanline":%d}\n',
                                   frames, nmi_count, nmi_min, nmi_max))
        result:close()
        emu.stop(0)
    end
end, emu.eventType.endFrame)

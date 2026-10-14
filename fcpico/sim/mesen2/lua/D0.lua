-- Capture the console-visible result of a fixed Doom stream after startup.
local output = assert(os.getenv("FCPICO_RESULTS"))
local limit = tonumber(os.getenv("FCPICO_FRAMES")) or 180
local frames = 0
local nmi_rti = os.getenv("FCPICO_NMI_RTI")
local nmi_count = 0
local nmi_max_scanline = -1
local nmi_min_scanline = 1000
local start_frame = tonumber(os.getenv("FCPICO_PRESS_START_FRAME"))
local start_polls = 0
local ui_apply = os.getenv("FCPICO_UI_APPLY")
local transition_frame = tonumber(os.getenv("FCPICO_UI_TRANSITION_FRAME"))
local transition_hex = os.getenv("FCPICO_UI_TRANSITION_PACKET")

if ui_apply then
    assert(transition_frame and transition_hex and #transition_hex == 32)
    emu.addMemoryCallback(function()
        if frames >= transition_frame then
            for index = 0, 15 do
                local value = tonumber(transition_hex:sub(index * 2 + 1, index * 2 + 2), 16)
                emu.write(0x0300 + index, value, emu.memType.nesDebug)
            end
        end
    end, emu.callbackType.exec, tonumber(ui_apply, 16))
end

if start_frame then
    emu.addEventCallback(function()
        local pressed = frames >= start_frame and frames < start_frame + 3
        emu.setInput({ start = pressed }, 0)
        if pressed then start_polls = start_polls + 1 end
    end, emu.eventType.inputPolled)
end

if nmi_rti then
    emu.addMemoryCallback(function()
        if frames >= limit - 30 then
            local scanline = emu.getState()["ppu.scanline"]
            nmi_count = nmi_count + 1
            nmi_max_scanline = math.max(nmi_max_scanline, scanline)
            nmi_min_scanline = math.min(nmi_min_scanline, scanline)
        end
    end, emu.callbackType.exec, tonumber(nmi_rti, 16))
end

local function save_ppu_bytes(name, first, count)
    local file = assert(io.open(output .. "/" .. name, "wb"))
    for address = first, first + count - 1 do
        file:write(string.char(emu.read(address, emu.memType.nesPpuDebug)))
    end
    file:close()
end

local function save_oam_bytes(name)
    local file = assert(io.open(output .. "/" .. name, "wb"))
    for address = 0, 255 do
        file:write(string.char(emu.read(address, emu.memType.nesSpriteRam)))
    end
    file:close()
end

local function save_cpu_bytes(name, first, count)
    local file = assert(io.open(output .. "/" .. name, "wb"))
    for address = first, first + count - 1 do
        file:write(string.char(emu.read(address, emu.memType.nesDebug)))
    end
    file:close()
end

emu.addEventCallback(function()
    frames = frames + 1
    if transition_frame and frames == transition_frame then
        local png = assert(io.open(output .. "/menu-open.png", "wb"))
        png:write(emu.takeScreenshot())
        png:close()
        save_oam_bytes("menu-open-oam.bin")
    end
    if frames == limit then
        local png = assert(io.open(output .. "/final.png", "wb"))
        png:write(emu.takeScreenshot())
        png:close()
        local prg = assert(io.open(output .. "/prg.bin", "wb"))
        for address = 0, 0x7fff do
            prg:write(string.char(emu.read(address, emu.memType.nesPrgRom)))
        end
        prg:close()
        save_ppu_bytes("palette.bin", 0x3f00, 16)
        save_ppu_bytes("attributes.bin", 0x23c0, 64)
        save_ppu_bytes("text_row.bin", 0x23A0, 32)
        save_ppu_bytes("sprite_chr.bin", 0x1000, 4096)
        save_ppu_bytes("background_chr.bin", 0x0000, 2048)
        save_ppu_bytes("sprite_pal.bin", 0x3f10, 16)
        save_oam_bytes("oam.bin")
        if nmi_rti then
            save_cpu_bytes("mailbox.bin", 0x20, 64)
            local file = assert(io.open(output .. "/mailbox.bin", "ab"))
            for address = 0xc0, 0xff do
                file:write(string.char(emu.read(address, emu.memType.nesDebug)))
            end
            file:close()
            save_cpu_bytes("ui_mailbox.bin", 0x0300, 16)
        end
        local result = assert(io.open(output .. "/lua_result.json", "w"))
        result:write(string.format('{"frames":%d,"nmi_count":%d,"nmi_min_scanline":%d,"nmi_max_scanline":%d,"start_polls":%d}\n',
                                   frames, nmi_count, nmi_min_scanline, nmi_max_scanline, start_polls))
        result:close()
        emu.stop(0)
    end
end, emu.eventType.endFrame)

-- SPDX-License-Identifier: BSD-3-Clause
-- Real tutorial ROM execution. Debug inspections must never consume cart bytes.
local output = assert(os.getenv("FCPICO_RESULTS"))
local limit = tonumber(os.getenv("FCPICO_FRAMES")) or 300
local startup = tonumber(os.getenv("FCPICO_STARTUP_FRAMES")) or 150
local inspect = os.getenv("FCPICO_DEBUG_PEEKS") ~= "0"
local frames = 0
local valid = 0
local samples = assert(io.open(output .. "/mailbox.csv", "w"))
samples:write("frame,magic\n")

local function save_screen(name)
    local png = assert(io.open(output .. "/" .. name .. ".png", "wb"))
    png:write(emu.takeScreenshot())
    png:close()
    local raw = assert(io.open(output .. "/" .. name .. ".argb", "wb"))
    for _, value in ipairs(emu.getScreenBuffer()) do
        raw:write(string.pack("<I4", value))
    end
    raw:close()
end

emu.addEventCallback(function()
    frames = frames + 1
    local magic = emu.read(0x21, emu.memType.nesDebug)
    samples:write(string.format("%d,%d\n", frames, magic))
    if frames > startup and magic == 0xfc then valid = valid + 1 end
    if inspect then
        -- Both pattern tables and nametables, deliberately on every frame.
        for address = 0, 0x2fff, 17 do emu.read(address, emu.memType.nesPpuDebug) end
    end
    if frames == startup then save_screen("startup") end
    if frames == limit then
        save_screen("final")
        samples:close()
        local result = assert(io.open(output .. "/lua_result.json", "w"))
        result:write(string.format('{"frames":%d,"valid_mailboxes_after_startup":%d}\n', frames, valid))
        result:close()
        emu.stop(0)
    end
end, emu.eventType.endFrame)

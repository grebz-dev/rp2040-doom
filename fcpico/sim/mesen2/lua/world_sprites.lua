-- SPDX-License-Identifier: BSD-3-Clause
-- Retain standard stream/NMI checks and three consecutive review captures.
local output = assert(os.getenv("FCPICO_RESULTS"))
local limit = assert(tonumber(os.getenv("FCPICO_FRAMES")))
local frames = 0
emu.addEventCallback(function()
    frames = frames + 1
    if frames == limit - 2 or frames == limit - 1 then
        local file = assert(io.open(output .. "/consecutive-" .. (frames - limit + 3) .. ".png", "wb"))
        file:write(emu.takeScreenshot())
        file:close()
    end
end, emu.eventType.endFrame)
dofile(assert(os.getenv("FCPICO_WORLD_BASE_LUA")))

--[[
    HajiSieu - Heartbeat script (Execute method)
    --------------------------------------------------------------
    Inject bang executor (Delta / Codex / Fluxus / Krnl ...) SAU KHI vao game.
    Cu vai giay, script ghi 1 file heartbeat de tool Python (rejoin_tool.py)
    biet instance con song. Neu executor crash / game disconnect -> heartbeat
    ngung cap nhat -> tool se tu dong rejoin instance do.

    LUU Y QUAN TRONG:
    - Executor chi cho `writefile` ghi vao THU MUC WORKSPACE cua no, KHONG ghi
      thang ra duong dan tuy y. Vi du Delta Android: workspace la
          /sdcard/Delta/Workspace
      -> file heartbeat se nam o:
          /sdcard/Delta/Workspace/HajiSieu/<username>.hb
    - Trong rejoin_tool.py, dat config:
          "method": "Execute"
          "usernames": { "<package>": "<TenNhanVatRoblox>" }   // map package -> username
          "executor_workspaces": [ "/sdcard/Delta/Workspace", ... ]  // neu khac mac dinh
      Tool se tim file <workspace>/HajiSieu/<username>.hb va kiem tra do "moi".
]]

local INTERVAL = 5            -- giay giua 2 lan ghi heartbeat
local FOLDER   = "HajiSieu"   -- thu muc con trong workspace cua executor

local Players = game:GetService("Players")

-- Tao thu muc neu executor ho tro
pcall(function()
    if typeof(makefolder) == "function" then
        if typeof(isfolder) ~= "function" or not isfolder(FOLDER) then
            makefolder(FOLDER)
        end
    end
end)

-- Ten file an toan tu username
local function keyName()
    local lp = Players.LocalPlayer
    local name = (lp and lp.Name) or "unknown"
    return (name:gsub("[^%w_%-]", "_"))
end

-- Ghi 1 heartbeat: epoch | username | userId | placeId
local function writeHeartbeat()
    if typeof(writefile) ~= "function" then
        return
    end
    local lp = Players.LocalPlayer
    local path = FOLDER .. "/" .. keyName() .. ".hb"
    local payload = string.format(
        "%d|%s|%s|%s",
        os.time(),
        tostring(lp and lp.Name or "?"),
        tostring(lp and lp.UserId or 0),
        tostring(game.PlaceId)
    )
    pcall(function()
        writefile(path, payload)
    end)
end

-- Doi LocalPlayer san sang
while not Players.LocalPlayer do
    task.wait(0.5)
end

-- Ghi ngay lan dau
writeHeartbeat()

-- Vong lap heartbeat (tu dung khi executor bi unload / game bi dong)
task.spawn(function()
    while true do
        writeHeartbeat()
        task.wait(INTERVAL)
    end
end)

-- (Tuy chon) dung heartbeat ngay khi phat hien bi kick / teleport that bai
pcall(function()
    game:GetService("GuiService").ErrorMessageChanged:Connect(function(msg)
        if msg and #tostring(msg) > 0 then
            -- khong ghi them -> file se cu dan -> tool rejoin
        end
    end)
end)

warn("[HajiSieu] Heartbeat dang chay. Ghi moi " .. INTERVAL .. "s vao " .. FOLDER .. "/" .. keyName() .. ".hb")

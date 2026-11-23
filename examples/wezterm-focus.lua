-- Optional module for the user's WezTerm config directory.
local wezterm = require("wezterm")
local function observe(window, pane, regained)
  if not window:is_focused() then return end
  local key = tostring(window:window_id()) .. "/" .. tostring(pane:pane_id())
  local previous = wezterm.GLOBAL.panebell_focus
  wezterm.GLOBAL.panebell_focus = key
  if not regained and (previous == nil or previous == key) then return end
  local client, session = pane:get_title():match("^pb%-view%-([a-z0-9]+)%-([%w_-]+)$")
  if not client or not session then return end
  local url = "panebell://focus/" .. client .. "/" .. session
  wezterm.background_child_process({"/bin/sh", "-c",
    '/usr/bin/pgrep -x PaneBell >/dev/null && /usr/bin/open -g "$1"', "panebell", url})
end
wezterm.on("window-focus-changed", function(window, pane) observe(window, pane, true) end)
wezterm.on("update-status", function(window, pane) observe(window, pane, false) end)
return {}

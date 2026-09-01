-- Test of the device side (app/src/main/assets/halo/katagent.lua) WITHOUT
-- glasses.
--
-- In place of the glasses there is a fake of the `frame` API. What is fed in is
-- exactly what Halo.kt puts on the wire — minus the leading 0x01, which the
-- glasses' Bluetooth stack strips before the Lua handler sees the data.
-- Checked: reassembly across several packets, dispatch to the handlers, the
-- display, the microphone, and how a recording ends.
--
-- Run (Lua is not on the host, hence the container):
--   docker run --rm -v /home/ulrich/katagent:/w -w /w alpine:latest \
--     sh -c "apk add --no-cache lua5.4 >/dev/null && lua5.4 tools/halo/test_frame_app.lua"

KATAGENT_TEST = true

-- The glasses store the modules flat as "data.min.lua" and load them with
-- require('data.min'); standard Lua would turn that into "data/min.lua".
-- So we register them under the dotted name directly — the same resolution as
-- on the device, without bending the file names.
local HALO = "app/src/main/assets/halo/"
local function preload(name)
        package.loaded[name] = dofile(HALO .. name .. ".lua")
end

-- ---- Fake of the glasses --------------------------------------------------
local sent = {}            -- what the glasses would have sent to the phone
local drawn = {}           -- lines on the display
local cleared = 0
local mic = { running = false, rate = nil, depth = nil, queue = {} }
local receive_cb = nil

frame = {
	HARDWARE_VERSION = "Halo",
	sleep = function(_) end,
	bluetooth = {
		max_length = function() return 241 end,
		send = function(s) sent[#sent + 1] = s; return true end,
		receive_callback = function(cb) receive_cb = cb end,
	},
	display = {
		text = function(s, x, y, color) drawn[#drawn + 1] = { s = s, x = x, y = y, color = color } end,
		show = function() end,
		clear = function(_) cleared = cleared + 1 end,
	},
	microphone = {
		start = function(args) mic.running = true; mic.rate = args.sample_rate; mic.depth = args.bit_depth end,
		stop = function() mic.running = false end,
		read = function(_)
			if not mic.running then return nil end
			if #mic.queue == 0 then return '' end
			return table.remove(mic.queue, 1)
		end,
	},
	camera = {
		-- capture_and_send() from the SDK calls several of these; for the test
		-- it is enough that they exist and do nothing.
		capture = function(_) end,
		read = function(_) return nil end,
		auto = function(_) end,
		image_ready = function() return true end,
	},
	file = { open = function() return { write = function() end, close = function() end } end },
	FILE = 0,
}

-- Load only after the frame fake: data.min registers its receive callback via
-- frame.bluetooth.receive_callback while loading.
for _, m in ipairs({ "data.min", "plain_text.min", "audio.min", "camera.min", "code.min" }) do
        preload(m)
end
dofile(HALO .. "katagent.lua")

-- ---- Helpers --------------------------------------------------------------
local failures = 0
local function check(name, cond, detail)
	if cond then
		print("  ok     " .. name)
	else
		failures = failures + 1
		print("  FAIL   " .. name .. (detail and ("  -> " .. detail) or ""))
	end
end

-- Builds the packets like Halo.kt (packets()), but already without the 0x01:
-- that is exactly how they reach the Lua handler.
local function packets(code, payload, max_data)
	max_data = max_data or 241
	local chunk = max_data - 1
	local out, sent_bytes, first = {}, 0, true
	repeat
		local rest = #payload - sent_bytes
		local take = first and math.min(rest, chunk - 2) or math.min(rest, chunk)
		local head
		if first then
			head = string.char(code, (#payload >> 8) & 0xFF, #payload & 0xFF)
		else
			head = string.char(code)
		end
		out[#out + 1] = head .. string.sub(payload, sent_bytes + 1, sent_bytes + take)
		sent_bytes = sent_bytes + take
		first = false
	until sent_bytes >= #payload
	return out
end

local function deliver(code, payload, max_data)
	for _, p in ipairs(packets(code, payload, max_data)) do
		receive_cb(p)
	end
end

local function text_payload(s, x, y, color, spacing)
	return string.char((x >> 8) & 0xFF, x & 0xFF, (y >> 8) & 0xFF, y & 0xFF, color, spacing) .. s
end

-- ---- Tests ----------------------------------------------------------------
print("device side (katagent.lua)")

check("the data handler is registered", receive_cb ~= nil)

-- 1) text in one packet
deliver(0x12, text_payload("hello glasses", 1, 1, 1, 4))
step()
check("short text lands on the display", #drawn == 1 and drawn[1].s == "hello glasses",
	drawn[1] and drawn[1].s or "nothing drawn")

-- 2) multi-line text -> several lines, y grows
drawn = {}
deliver(0x12, text_payload("line one\nline two\nline three", 1, 1, 1, 4))
step()
check("newlines become display lines", #drawn == 3)
check("the lines move down", #drawn == 3 and drawn[2].y > drawn[1].y,
	#drawn == 3 and (drawn[1].y .. " -> " .. drawn[2].y) or "too few lines")

-- 3) long text over many packets: arrives complete
drawn = {}
local long = string.rep("A", 1200)
deliver(0x12, text_payload(long, 1, 1, 1, 4), 64)
step()
check("long text is reassembled from many packets",
	#drawn == 1 and #drawn[1].s == 1200,
	#drawn == 1 and ("length " .. #drawn[1].s) or ("lines: " .. #drawn))

-- 4) clear
cleared = 0
deliver(0x10, "")
step()
check("clearing wipes the display", cleared == 1, "cleared=" .. cleared)

-- 5) microphone on, with the phone's values
deliver(0x30, string.char(0x1F, 0x40, 16))          -- 8000 Hz, 16 bit
step()
check("microphone runs at 8000 Hz / 16 bit",
	mic.running and mic.rate == 8000 and mic.depth == 16,
	tostring(mic.rate) .. "/" .. tostring(mic.depth))

-- 6) what was recorded travels to the phone with the progress flag
sent = {}
mic.queue = { "abc", "def" }
step(); step()
local audio_chunks = 0
for _, s in ipairs(sent) do
	if string.byte(s, 1) == 0x05 then audio_chunks = audio_chunks + 1 end
end
check("audio goes to the phone with 0x05", audio_chunks == 2, "chunks: " .. audio_chunks)

-- 7) stop: final chunk 0x06, after that the recording rests
sent = {}
deliver(0x31, "")
step()
local final = false
for _, s in ipairs(sent) do
	if string.byte(s, 1) == 0x06 then final = true end
end
check("stop sends the final chunk 0x06", final)
check("nothing runs after the stop", mic.running == false)

-- 8) an unknown code must not kill the loop
local ok = pcall(function() deliver(0x77, "whatever"); step() end)
check("unknown messages are dropped quietly", ok)

print(failures == 0 and "ALL TESTS GREEN" or (failures .. " FAILURES"))
os.exit(failures == 0 and 0 or 1)

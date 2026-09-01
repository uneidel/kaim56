-- kAIm56 KatAgent -- device side for the Brilliant glasses (Halo/Frame)
-- Copyright (C) 2026 Ulrich Neidel
-- SPDX-License-Identifier: AGPL-3.0-or-later
--
-- Counterpart to Halo.kt: takes the phone's messages, shows text, switches
-- microphone and camera. The building blocks (data, plain_text, audio, camera,
-- code) come unchanged from the Brilliant SDK (BSD-3-Clause, see
-- LICENSE.brilliant_sdk) -- which is why text and clear follow their message
-- format.
--
-- ASCII ONLY in this file: the Lua runtime reads the source as latin-1, an
-- em dash in a comment is already enough to break loading.
--
-- Testable without glasses: the loop body sits in step(), and app_loop() only
-- starts when KATAGENT_TEST is unset (see tools/halo/test_frame_app.lua).

local data = require('data.min')
local plain_text = require('plain_text.min')
local audio = require('audio.min')
local camera = require('camera.min')
local code = require('code.min')

-- Phone -> glasses (has to match HaloSession.Code in Halo.kt)
TEXT_MSG = 0x12
CLEAR_MSG = 0x10
AUDIO_START_MSG = 0x30
AUDIO_STOP_MSG = 0x31
PHOTO_MSG = 0x0d

-- is the microphone running? Then the loop shovels audio to the phone.
streaming = false

local parsers = {}
parsers[TEXT_MSG] = plain_text.parse_plain_text
parsers[CLEAR_MSG] = code.parse_code
parsers[AUDIO_STOP_MSG] = code.parse_code
parsers[PHOTO_MSG] = code.parse_code
-- The microphone start carries sample rate (16 bit) and bit depth.
parsers[AUDIO_START_MSG] = function(d)
	return {
		sample_rate = string.byte(d, 1) << 8 | string.byte(d, 2),
		bit_depth = string.byte(d, 3),
	}
end

function clear_display()
	if frame.HARDWARE_VERSION == "Frame" then
		frame.display.text(" ", 1, 1)
		frame.display.show()
	else
		frame.display.clear(0x000000)
	end
end

-- Show the agent's reply. Newlines become lines; the glasses do not wrap on
-- their own, so the phone pre-wraps the text.
function print_text(parsed)
	local i = 0
	for line in parsed.string:gmatch("([^\n]*)\n?") do
		if line ~= "" then
			if frame.HARDWARE_VERSION == "Frame" then
				frame.display.text(line, 1, i * 60 + 1)
			else
				frame.display.text(line, 1, i * 20 + 1, parsed.color)
			end
			i = i + 1
		end
	end
	if frame.HARDWARE_VERSION == "Frame" then
		frame.display.show()
	end
end

local handlers = {}

handlers[TEXT_MSG] = function(parsed)
	if parsed.string ~= nil then
		print_text(parsed)
	end
end

handlers[CLEAR_MSG] = function(_)
	clear_display()
end

handlers[AUDIO_START_MSG] = function(parsed)
	audio.start({ sample_rate = parsed.sample_rate, bit_depth = parsed.bit_depth })
	streaming = true
end

handlers[AUDIO_STOP_MSG] = function(_)
	-- ONLY stop the microphone. streaming stays on until the loop reads once
	-- more: read_and_send_audio() then gets nil, sends the phone the final
	-- chunk (0x06), and only after that does the recording rest. Clearing
	-- streaming right here swallows the final chunk -- the phone would wait
	-- forever for the end of the recording.
	audio.stop()
end

handlers[PHOTO_MSG] = function(_)
	camera.capture_and_send({})
end

-- One pass of the main loop. Split out so the test can call it on its own
-- without hanging in an endless loop.
function step()
	local items = data.process_raw_items()
	for i = 1, #items do
		local flag = items[i][1]
		local raw = items[i][2]
		if parsers[flag] then
			local parsed = parsers[flag](raw)
			if handlers[flag] then
				handlers[flag](parsed)
			end
		end
	end
	if streaming then
		-- nil = the microphone was stopped and the final chunk is out
		if audio.read_and_send_audio() == nil then
			streaming = false
		end
	end
end

function app_loop()
	clear_display()
	print("KatAgent ready")
	while true do
		local rc, err = pcall(step)
		if rc == false then
			print(err)
			clear_display()
			frame.sleep(0.04)
			break
		end
		-- Tight cadence during a recording, frugal otherwise.
		frame.sleep(streaming and 0.001 or 0.1)
	end
end

if not KATAGENT_TEST then
	app_loop()
end

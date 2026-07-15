package main

import "core:fmt"
import "core:os"
import "core:encoding/json"
import "core:path/filepath"
import "core:time"
import rl "vendor:raylib"

WINDOW_WIDTH  :: 1920
WINDOW_HEIGHT :: 515
WINDOW_TITLE  :: "UVULITES - Live Social Media Counter"

FETCH_INTERVAL :: 1.0 // seconds between reads of followers.json

// How often the Instagram service rewrites followers.json — keep in sync with
// POLL_INTERVAL in instagram_service/poll_followers.py (default 60).
POLL_INTERVAL :: 60

// Seconds past the expected update before we call it overdue — covers the
// service's API request time and our once-per-second file read lag.
OVERDUE_GRACE :: 10

Counter :: struct {
	followers_count: int,
	username:        string,
	ts:              int,
	error:           bool,
}

// Last known-good data — what the window displays.
counter: Counter

main :: proc() {
	rl.InitWindow(WINDOW_WIDTH, WINDOW_HEIGHT, WINDOW_TITLE)
	// InitWindow doesn't abort on failure; if the GL context couldn't be
	// created, calling CloseWindow below would segfault on an uninitialized
	// render batch. Bail out early instead. On the Pi 4 the usual cause is the
	// V3D driver only exposing desktop GL 3.1 — see run.sh for the fix.
	if !rl.IsWindowReady() {
		fmt.eprintln("Failed to open a window: no usable OpenGL context.")
		fmt.eprintln("On Raspberry Pi, launch with ./run.sh (sets MESA_GL_VERSION_OVERRIDE=3.3).")
		return
	}
	defer rl.CloseWindow()

	rl.SetTargetFPS(60)

	data_stale := true
	last_fetch: f64 = -FETCH_INTERVAL // negative so the first frame fetches immediately

	for !rl.WindowShouldClose() {
		// --- Update ---
		if now := rl.GetTime(); now - last_fetch >= FETCH_INTERVAL {
			last_fetch = now

			stats := fetch_json()
			data_stale = stats.error
			if !stats.error && stats.ts > counter.ts {
				delete(counter.username) // release the string from the previous update
				counter = stats
			} else {
				// Errored, unchanged, or older than what we already show — discard it.
				delete(stats.username)
			}
		}

		// --- Draw ---
		rl.BeginDrawing()
		defer rl.EndDrawing()

		rl.ClearBackground(rl.RAYWHITE)

		if counter.ts == 0 {
			// Never fetched successfully yet.
			draw_centered("Waiting for data...", (WINDOW_HEIGHT - 60) / 2, 60, rl.GRAY)
		} else {
			draw_centered(fmt.ctprintf("%s", counter.username), 100, 50, rl.GRAY)
			draw_centered(fmt.ctprintf("%d", counter.followers_count), 190, 220, rl.DARKGRAY)

			// Countdown to the service's next write. Derived from counter.ts
			// every frame, so a fresh ts resets it automatically and it can
			// never drift out of sync with the data on screen.
			remaining := counter.ts + POLL_INTERVAL - int(time.to_unix_seconds(time.now()))
			switch {
			case remaining > 0:
				draw_centered(fmt.ctprintf("next update in %ds", remaining), 440, 30, rl.GRAY)
			case remaining > -OVERDUE_GRACE:
				draw_centered("update due...", 440, 30, rl.GRAY)
			case:
				draw_centered("update overdue", 440, 30, rl.ORANGE)
                data_stale = true
			}
		}

		if data_stale {
			rl.DrawText("STALE", 20, 20, 30, rl.RED)
		}

		free_all(context.temp_allocator) // frees the ctprintf strings
	}
}

// Draw text horizontally centered in the window.
draw_centered :: proc(text: cstring, y, size: i32, color: rl.Color) {
	width := rl.MeasureText(text, size)
	rl.DrawText(text, (WINDOW_WIDTH - width) / 2, y, size, color)
}

// Fetch the JSON from followers.json and return it as a Counter.
// On any failure, returns Counter{error = true}.
fetch_json :: proc() -> Counter {
	// Resolve followers.json relative to the executable, not the working directory.
	exe_dir, dir_err := os.get_executable_directory(context.allocator)
	if dir_err != nil {
		fmt.println("Error getting executable directory:", dir_err)
		return Counter{error = true}
	}
	defer delete(exe_dir)

	json_path, join_err := filepath.join({exe_dir, "instagram_service", "followers.json"})
	if join_err != nil {
		fmt.println("Error building JSON path:", join_err)
		return Counter{error = true}
	}
	defer delete(json_path)

	data, read_err := os.read_entire_file(json_path, context.allocator)
	if read_err != nil {
		fmt.println("Error reading file:", json_path, read_err)
		return Counter{error = true}
	}
	defer delete(data)

	// load this data into counter
	c: Counter
	if err := json.unmarshal(data, &c); err != nil {
		fmt.println("Error parsing JSON:", err)
		return Counter{error = true}
	}

	return c
}

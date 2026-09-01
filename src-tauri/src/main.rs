// CaraiOS Desktop — Tauri v2 Application
// Agency OS Master Plan §8: Tauri Desktop Packaging
//
// This is the Tauri v2 entry point. It:
// 1. Starts the FastAPI backend as a sidecar process on port 8000
// 2. Opens the web UI in a native window
// 3. Provides system tray, notifications, and auto-update
// 4. Runs fully offline in Micro profile

// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    caraios_lib::run()
}
//! Embed the icon and the application manifest (per-monitor DPI awareness,
//! common controls 6, Windows 10 and 11) into Sovereign.exe.

fn main() {
    println!("cargo:rerun-if-changed=sovereign.rc");
    println!("cargo:rerun-if-changed=sovereign.manifest");
    println!("cargo:rerun-if-changed=../Sovereign.ico");
    embed_resource::compile("sovereign.rc", embed_resource::NONE)
        .manifest_required()
        .unwrap();
}

# Sovereign identity

The light and dark navbar SVGs are the approved Sovereign logo kit, imported
without changing geometry, spacing, colors or the outlined wordmark. Use the
dark variant on dark surfaces and the light variant on light surfaces.

Symbol exports retain the original `symbol` group and gradients, with only a
square canvas. The app/browser icon adds a navy rounded tile for contrast.
No fonts, external resources or generative images are required. The native
`desktop/macos/Resources/Sovereign.icns` is rasterized from this icon at the
standard macOS icon sizes. Treat these as branding assets, not trust badges.

The Sovereign Horizon backdrop (`../assets/starry_bg.webp`, 96 KB WebP) is
bundled locally like the logos; it is decorative and always sits under a
scrim so text over it keeps AA contrast. Noon omits the photo.

Ubuntu setup bundles the same image as a lossless PNG decoding at
`desktop/linux-setup/starry-background.png`, so native GTK needs no additional
WebP loader. It is decorative, uses a dark overlay, and is omitted with the
brand CSS when setup launches in a high-contrast theme. Its package build copies
the checked-in bytes without converting or downloading artwork.

The PNG was decoded without resizing or re-encoding the original image's lossy
content, using libwebp `dwebp` 1.6.0:

```sh
dwebp capability/web/src/assets/starry_bg.webp -o desktop/linux-setup/starry-background.png
```

Source WebP SHA-256:
`3b87bfd08455c4a2469bfd5e4c7317633349a7b624cb95f8c90599dc0679c4ef`.
Native PNG SHA-256:
`ea1455ccd691fa99c67fff6fcf7086c4f6e9d6cd10ee0eff08eabada84c8107e`.

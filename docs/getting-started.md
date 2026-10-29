# Your first model with Sovereign

Sovereign helps you find a model, keep its complete files, and recover them if
their original host disappears. You do not need Skulk, an account or a GPU to
keep or recover a model. Running it later needs a compatible model application.

These instructions cover core 0.26 / Library 0.22, Mac build 8 and Linux revision 3.
The first-launch choices, Getting started help, recipient instructions and
Share → Save kit to send require that matching cohort. Older native installers
retain their original controls. The independent [Python desktop profile](installation.md#select-the-independent-desktop-profile)
is also available without a native app or Skulk.

## Install and open

- **Apple silicon Mac:** download the DMG from the
  [public Mac release](https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/tag/sovereign-macos-v0.1.0-build8),
  open it, drag **Sovereign** to **Applications**, then open Sovereign normally.
  macOS checks the app's Developer ID and notarization. See
  [Mac installation](macos-distribution.md) for supported systems and independent
  release verification.
- **Ubuntu 24.04, amd64 or arm64:** follow the
  [graphical setup](linux-distribution.md#graphical-setup-recommended), then open
  **Sovereign** from your applications menu. Setup checks the app and update key,
  then Ubuntu checks signed software updates. No Terminal commands, Skulk or
  Python setup are required. The guide also explains how to independently verify
  the initial setup download; that optional advanced check uses Terminal.

Installation and first launch start no model download or sharing. Choose what
you want to do: browse models, open a recovery kit, or receive a sharing file.
The **Getting started** info button keeps the complete journey available later.
Existing saved models reopen in **Library**.

## Find something useful and keep it

1. Choose **Browse public models**. This contacts Foxlight for signed descriptions
   and file information, rather than downloading model weights. Other publishers
   can provide independent source files or sharing files. Searches stay on your
   device and work offline after listings have been saved.
2. Search by a name or a use such as writing, images or voice. Read **Works with**
   and **Produces**: understanding a picture differs from generating one. Check
   the supplied description and limitations. A signed listing does not test the
   model's quality or whether it runs on your computer.
3. Choose a file version supported by your intended model application. Different
   formats and precisions are alternatives, not parts you must all download.
   Ready to review means the listing has complete supported file verification
   information; a supplier can still be offline.
4. Choose **Keep a copy**. Review the whole model, required companions, download
   total and free working space. Download size is storage, not runtime memory.
   Downloads may need more temporary space than the finished copy.
5. Confirm the exact private selection. **Activity** shows the work and outcome.
   Files and signatures are checked before a complete copy appears in Library.
   A failed check cannot be bypassed. Receiving starts no sharing or model code.

If a listing cannot be downloaded completely, choose another ready version or
ask its publisher for a complete kit. Installing Skulk is not required to use
Sovereign's independent recovery path.

## Protect it beyond this computer

In **Library**, choose **Save recovery kit**. Save the resulting `.foxkit` on
another drive. This one file contains the complete captured model, companions,
original model cards, unknown card properties and signed provenance.

Alternatively, **Backups** remembers a connected folder. Choose **Back up** on a
saved model and review that destination. A completed backup is read back and
verified. Remembering a folder does not schedule backups automatically. A missing
drive stops the operation; it does not redirect it somewhere else.

A kit is not encrypted. A second file on the same device does not protect against
losing that device. Original files and older backups remain under your control.

## Send a copy to someone

**Share → Save kit to send** prepares a complete kit for your own drive or
file-transfer service. Review the capture before sending: its original documents
and metadata may contain private details. The recipient can recover after your
computer goes offline. Sovereign sends no message and starts no mirror for you.

**Recovery instructions** gives you the exact archive and public verification-key
identifiers for this saved copy, with a copy control for your own note. Keep the
instructions separately. Confirm the full key with the recipient through an
existing trusted conversation. A key delivered beside an unfamiliar kit does not
prove who sent it. These public identifiers reveal no private signing key.

For a separate public mirror, choose **Share → Review public mirror**. You must
review public files and redistribution terms before preparing a public copy,
then separately review and approve a finite serving session. A mirror needs a
reachable, awake computer. Direct HTTP sharing is not encrypted or anonymous;
Sovereign does not configure a router or prove external reachability. A
`.foxhandoff` sharing file refers to a supplier; it does not contain model files
and cannot replace an offline recovery kit.

## Recover, including on a replacement computer

1. Install Sovereign and choose **Open recovery kit**, or **Choose a recovery
   kit** on the first-launch screen. Select the `.foxkit` file. The original
   registry, sender and Skulk Core can all be unavailable.
2. Compare the complete verification key with the sender through a separately
   trusted channel. Review the exact capture, then explicitly accept that key
   for this copy. Software signing and capture acceptance are separate. This
   choice grants no registry approval, upstream authorship or code permission.
3. Choose **Import verified copy**. After all checks succeed, open **Library**.
4. Choose **Restore files**. Activity offers **Open restored folder** on success.
   The folder contains the captured model, companions, cards and evidence. No
   existing installation is replaced and no runtime is installed or executed.

A sharing file follows a different path: opening it is offline, but obtaining
its model needs a reviewed connection to a reachable supplier and separate
capture-key acceptance. After receiving, save a complete recovery kit if you want
to recover without that supplier later.

## When something goes wrong

- **Not enough working space:** free space or choose a smaller complete model,
  then review again. Do not confuse storage with model-running memory.
- **Interrupted work:** Activity records what happened. Reopening never silently
  repeats a download or starts sharing. Completed copies remain available.
- **Disconnected backup drive:** reconnect the original drive and review again.
  Your Library copy is kept.
- **Unfamiliar or changed verification information:** confirm it independently.
  Do not accept a key merely to get past a prompt. Invalid signatures or damaged
  files cannot be treated as a complete recovery.

The current releases are previews. Successful technical tests are distinct from
observed ease of use with nontechnical participants and from runtime, privacy or
community-mirror qualification.

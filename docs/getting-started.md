# Your first model with Sovereign

Sovereign helps you find a model, keep its complete files, and recover them if
their original host disappears. You do not need Skulk, an account or a GPU to
keep or recover a model. Running it later needs a compatible model application.

These instructions cover core 0.38.1 / Library 0.34.1 on Linux revision 18, and Mac
build 22 (core 0.38.0 / Library 0.34.0) until Mac build 23 is published.
The first-launch choices, Getting started help, recipient instructions,
Share → Save kit to send and **Help keep models available** on the Sharing screen
require that matching cohort. Older native installers
retain their original controls. The independent [Python desktop profile](installation.md#select-the-independent-desktop-profile)
is also available without a native app or Skulk.

## Install and open

- **Apple silicon Mac:** download the DMG from the
  [public Mac release](https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/tag/sovereign-macos-v0.1.0-build22),
  open it, drag **Sovereign** to **Applications**, then open Sovereign normally.
  macOS checks the app's Developer ID and notarization. See
  [Mac installation](macos-distribution.md) for supported systems and independent
  release verification. Intel Macs are not supported.
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
   can provide independent source files or sharing files. Searching the saved
   Foxlight listings happens on your device and works offline. Find also sends
   your search words to Hugging Face unless you turn that off (see
   [Search sources](#search-sources)), and it reads the lists community mirrors
   publish (see [Models kept by community mirrors](#models-kept-by-community-mirrors)).
2. Search by a name or a use such as writing, images or voice. Read **Works with**
   and **Produces**: understanding a picture differs from generating one. Check
   the supplied description and limitations. A signed listing does not test the
   model's quality or whether it runs on your computer.
3. Choose a file version supported by your intended model application. Different
   formats and precisions are alternatives, not parts you must all download.
   Find shows only listings Sovereign can save completely. Turn on **Also show
   listings Sovereign can't save yet** to see the others; a supplier can still
   be offline.
4. Choose **Keep a copy**. **Review before saving** shows the whole model,
   required companions, download size and free working space. Download size is
   storage, not runtime memory. If a licence has conditions, such as
   non-commercial use, tick its acceptance under **Terms to accept**.
5. Choose **Keep a copy** at the bottom of the review to start. **Activity**
   shows the work and outcome. Files and signatures are checked before a
   complete copy appears in Library. A failed check cannot be bypassed. Saving
   starts no sharing or model code.

### Follow a save in Activity

Each save shows a ring for every stage, for example **Download**, **Check &
sign** and **Store**. The current stage shows its percentage, how much is done,
the download speed and about how much time is left. **Stop** ends the save.

If a save was interrupted, choose **Keep a copy** on the same model again. It
picks up where it stopped: Sovereign first reads back the part it already
downloaded, to confirm every byte, then downloads only the rest. **Stop** works
during that check too.

Finished entries stay in Activity until you remove them. **Remove from
Activity** removes one; **Clear finished** removes every finished entry after a
second click. Neither deletes a saved model. Removing a finished **Save
recovery kit** entry also deletes the kit file Sovereign kept for that
download.

After a save of a model whose licence allows sharing, Activity asks once:
**Help keep this model available?** It asks only while sharing is off.
**Review sharing** opens the [sharing review](#help-keep-models-available) with
that model in mind; nothing is shared until you agree there. **Not now** never
asks again.

### Search sources

Find searches the saved Foxlight listings on your device. Beside the search box,
**Also search** has two switches:

- **Hugging Face** is on by default, so any public model there can be kept, not
  only the saved listings. Your search words go to huggingface.co. Nothing is
  sent until you type a search.
- **Pirate Face** is off by default. Pirate Face lists community torrents of
  Hugging Face models, including models Hugging Face has removed. With it on,
  your search words go to pirateface.co.

The same choices are under **Settings → Search sources and privacy**, together
with **Swarm downloads** (off by default), which lets Sovereign download from
public BitTorrent swarms. Other people in a swarm can see your computer's
address. Swarm downloads only download: they share nothing, and nothing keeps
running after the save.

Choose **Choose files** on a Hugging Face result. Sovereign pins the model to
its current exact version and shows its licence and files. For a GGUF model, pick a version
(Q4_K_M is a good balance of size and quality); otherwise Sovereign selects the
model's files and skips extra copies of the same weights in other formats. You
can change the selection. **Keep a copy** then opens the same review as any
other save. Every file is checked against the fingerprint Hugging Face lists for
that exact version, and if Hugging Face can't provide a file, Sovereign asks
community mirrors for the same file by its fingerprint.

A model straight from Hugging Face isn't a reviewed listing. Some models ask for
a Hugging Face account and permission first. If you've been given access, add
your Hugging Face token under **Settings → Search sources and privacy**: create a
**read** token at
huggingface.co under Settings → Access Tokens and paste it in. A token also
raises Hugging Face's download limits. Sovereign checks it with Hugging Face,
keeps it in your computer's keychain, shows only your account name afterwards,
and sends it only to huggingface.co, never to mirrors. Remove it there at any
time.

### Models kept by community mirrors

People running Sovereign mirrors publish a signed list of the models they keep,
so a model stays findable after its original host removes it. The first time
Find shows its model list in a session, Sovereign reads these lists; those
mirrors can see your computer's address. Find then shows the models, marked
**Kept by N mirrors**. Searching the lists happens on your device.

With **Swarm downloads** on, Find also reads the lists of computers that share
models over BitTorrent without an address anyone can reach, marked **Kept by N
computers over BitTorrent**. Reading those lists joins the public BitTorrent
network (its DHT and the list's swarm), which shows your computer's address to
other people there.

Choosing **Keep a copy** checks where the copy says it came from against
Hugging Face. The review tells you whether the files match, differ, or couldn't
be confirmed because Hugging Face no longer has them. Every file is checked
against its fingerprint while it downloads, and the copy is kept under your own
key.

If a listing cannot be downloaded completely, choose another ready version or
ask its publisher for a complete kit. Installing Skulk is not required to use
Sovereign's independent recovery path.

## Protect it beyond this computer

In **Library**, choose **Save recovery kit**. Save the resulting `.foxkit` on
another drive. This one file contains the complete captured model, companions,
original model cards, unknown card properties and signed provenance.

Alternatively, add a connected drive or mounted NAS folder under **Settings →
Storage → Backup folders → Add backup folder**. Then choose **Back up** on a
saved model and review that destination. A completed backup is read back and
verified. Remembering a folder does not schedule backups automatically. A missing
drive stops the operation; it does not redirect it somewhere else.

A kit is not encrypted. A second file on the same device does not protect against
losing that device. Original files and older backups remain under your control.

## Your Library and Settings

**Library** lists your saved models. **Remove from Library** on a saved model
deletes that copy and its files after you confirm; it can't be undone. Recovery
kits and backups on other drives are kept, so you can bring the model back from
one of those. If the model was shared through **Help keep models available**,
Sovereign stops sharing it unless another saved copy provides the same model.

**Settings** has these choices, plus a **Sharing** link to the Sharing screen:

- **Storage → Library location:** where model copies are kept. Choose **Change
  location** to use any connected drive, mounted network share (NAS) or folder.
  New saves go there. Copies you already have keep working where they are, and
  Sovereign offers to move them: each file is read back from the new drive
  before the original is removed. If the drive is disconnected, saving stops
  and asks you to connect it; Sovereign never falls back to this computer's
  drive unless you choose that.
- **Storage → Backup folders:** the drives and folders **Back up** can use.
- **Search sources and privacy:** Hugging Face search, your Hugging Face token,
  Pirate Face search and swarm downloads.
- **Appearance → Theme:** light or dark.

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
then separately review and approve a one-hour serving session. A mirror needs a
reachable, awake computer. Direct HTTP sharing is not encrypted or anonymous.
For this one-hour session Sovereign does not configure your router or prove
external reachability. (**Help keep models available**, below, is different: it
can ask your router to open a port.) A `.foxhandoff` sharing file refers to a
supplier; it does not contain model files and cannot replace an offline
recovery kit.

## Help keep models available

**Help keep models available**, on the **Sharing** screen, lets this computer
share your saved models with anyone who wants their own verified copy, for as
long as you keep it on. It is off until you turn it on. Everyone who downloads
checks every byte, so nobody has to trust your computer.

**Turn on** opens **Share your backups with everyone**. It has these steps:

1. **What to share:** every saved model whose licence allows it (new ones join
   on their own, and you can keep any one back), or only the ones you choose.
2. **Licences that allow sharing:** a model is shared only if every part has a
   licence that is switched on. Licences are grouped in families such as
   permissive, copyleft and non-commercial. Models with no licence, or an
   unknown one, always stay private.
3. **How people find this computer:**
   - **Public address (optional):** only if people outside your network reach
     this computer through an address of your own, such as a port forward or a
     reverse proxy. Leave it empty otherwise.
   - **Announce on the BitTorrent DHT** (on by default): publishes this
     computer's public address and mirror port on the public BitTorrent DHT as
     a Sovereign mirror, only while people outside your network can reach it.
   - **Publish to Nostr relays** (on by default): publishes this computer's
     signed card, with its address (and your cloud storage address, if any), to
     the public Nostr relays listed there, when it can be reached. While models
     are shared over BitTorrent, it also publishes a signed listing card saying
     which models this computer shares, even when it can't be reached directly.
     The relays see your computer's address when it publishes or reads.
4. **Pirate Face:** **Also share on Pirate Face**, off by default. Where a
   shared model's Pirate Face torrent holds exactly the files you share, your
   computer shares that torrent too, from the same files. Nothing is
   downloaded, and people in that swarm see your computer's address.
5. **What others can see:**
   - Anyone who connects can download the shared models and sees this
     computer's address. Direct sharing isn't anonymous or encrypted.
   - Shared models are also shared over BitTorrent, one file at a time, so
     people can download them even when your computer can't be reached
     directly. People downloading that way see your computer's address too.
   - **Limit uploads** is off by default: uploads are counted but never
     stopped. Turn it on to set a daily and a monthly limit, which covers
     direct and BitTorrent sharing alike; sharing pauses when one is reached
     and resumes when it renews.
   - **Ask my router to open port …** (on by default, shown only when you
     didn't enter a public address): Sovereign asks your router (UPnP /
     NAT-PMP) to forward the sharing port, and the port BitTorrent sharing uses,
     to this computer while sharing is on. Turn it off to leave your router
     exactly as it is.
   - Sharing keeps running after you restart this computer, for one year, then
     asks again.

Last comes one consent line that names what you chose, for example: "Keep
sharing these models from this computer until I turn it off, and share them
over BitTorrent, listed on the BitTorrent DHT, and announce this computer
publicly on the BitTorrent DHT and 5 Nostr relays, and ask my router to open
port 8743." **Start sharing** is available once you tick it.

Once BitTorrent sharing is on, two things happen whatever you chose under **How
people find this computer**: your computer takes part in the public BitTorrent
DHT, so anyone looking up one of the shared files finds your computer among the
people sharing it; and the files you share are listed on the DHT under this
computer's own key, so anyone can look up which models it shares. The DHT
switch covers only announcing this computer's address as a mirror.

While sharing, the panel says how many models and files are shared, how much
was uploaded today and this month, whether the router opened the port, refused,
or opened it behind an internet provider that shares one address among many
customers (so people outside still can't reach this computer directly), and
whether each announcement is happening. **Change what's shared** reopens the
same steps. **Turn off** stops sharing at once; nothing is deleted.

The consent lasts one year. Until it ends or you turn it off, opening Sovereign
resumes sharing, the router request and the announcements without asking again.

If you agreed to sharing in an earlier version, the panel shows **Review
sharing over BitTorrent**: nothing is shared over BitTorrent until you agree to
it there.

## What becomes public, and how to turn it off

| What | Who can see it | On by default? | How to turn it off |
| --- | --- | --- | --- |
| Hugging Face search | huggingface.co sees your search words and your computer's address | Yes | **Also search: Hugging Face** in Find, or **Settings → Search sources and privacy** |
| Pirate Face search | pirateface.co sees your search words and your computer's address | No | **Also search: Pirate Face** in Find, or **Settings → Search sources and privacy** |
| Swarm downloads | Other people in a public BitTorrent swarm, and the DHT, see your computer's address | No | **Settings → Search sources and privacy → Swarm downloads** |
| BitTorrent sharing and its DHT listing | Anyone can look up which models your computer shares; people downloading see its address | Only after you agree to **Help keep models available** | **Turn off** under **Sharing → Help keep models available** |
| DHT mirror announcement | Anyone can look up your computer's public address as a Sovereign mirror | Yes, inside the sharing consent | **Announce on the BitTorrent DHT** under **Change what's shared** |
| Nostr cards and listing cards | Anyone can read your mirror's address, or which models your computer shares; relays see your address | Yes, inside the sharing consent | **Publish to Nostr relays** under **Change what's shared** |
| Pirate Face sharing | People in that Pirate Face torrent's swarm see your computer's address | No | **Also share on Pirate Face** under **Change what's shared** |
| Router port mapping | Your router opens ports so people outside can reach this computer | Yes, inside the sharing consent, when no public address is entered | **Ask my router to open port …** under **Change what's shared** |
| One-hour public copy | Anyone who can reach the address can download that copy and sees your computer's address | No | **Stop** in Activity; it also ends on its own after one hour |

Some contacts happen whatever you choose: updating listings contacts
registry.foxlight.ai, reading community mirror lists shows your address to those
mirrors, and every download shows your address to the site that sends the
files. Saving, backing up and restoring never share anything.

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
  repeats a download; choose **Keep a copy** again to continue a save. Reopening
  starts sharing only if **Help keep models available** is on. Completed copies
  remain available.
- **Disconnected backup drive:** reconnect the original drive and review again.
  Your Library copy is kept.
- **Unfamiliar or changed verification information:** confirm it independently.
  Do not accept a key merely to get past a prompt. Invalid signatures or damaged
  files cannot be treated as a complete recovery.

The current releases are previews. Successful technical tests are distinct from
observed ease of use with nontechnical participants and from runtime, privacy or
community-mirror qualification.

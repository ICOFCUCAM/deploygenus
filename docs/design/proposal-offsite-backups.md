# DeployPro — Proposal: backups copied off the server

**Status:** **RECOMMENDED 2026-10-02, the owner agreed ("DONE" after the list that put this next), and implemented.**
**Builds on (locked, not reopened):** `proposal-system-disk-and-backups.md`, i.e. the daily backup, Back up now, the System page facts and the setup checklist.

**Why:** a backup on the server it protects is lost with that server. The setup checklist has always said "copy it off", but the only way was rsync or rclone on a cron, set up by hand on the server.

---

## 1. What the owner does, once

On **System → Disk and backups → Off-server copy**:

1. In Hetzner Console, create a Storage Box (BX11 is enough). Turn on **SSH support** and set its password.
2. Enter its username (`u123456`). The address (`u123456.your-storagebox.de`), port 23 and folder `deploypro` fill themselves in. Any other SFTP server works with its address and port.
3. Enter the Storage Box password once and press **Install key and connect**.

From then on, every backup (daily, or Back up now) is copied there. The page says when the last copy happened, or why it failed.

## 2. Decisions (recommended answers taken)

- **D1 · Protocol: SFTP with the `sftp` client already in the image.** There is nothing new to install. A Storage Box speaks SFTP on port 23. rsync would need a new package and gives little gain for a once-a-day copy.
- **D2 · Credentials: DeployPro's own Ed25519 key, encrypted with the master key.**
  - The password is used once, to add that key to the box's `.ssh/authorized_keys`, and is never stored or logged.
  - The owner's own lines in that file are kept. DeployPro's earlier key (from turning it off and on again) is replaced, so an old key never keeps access.
  - The owner can instead paste the shown public key themselves and press **Test connection**.
- **D3 · Host key: trusted on first contact, then pinned.** If it changes, copies stop with *"…is not the server DeployPro first connected to"*. Saving the settings for a different server learns the new key.
- **D4 · Never a mirror.** Each copy is uploaded under `.partial` and renamed when complete. Pruning removes only finished `deploypro-…` copies beyond the newest `DEPLOYPRO_BACKUP_KEEP` (7). Anything else in the folder is never touched. An emptied backup directory on the server deletes nothing on the box.
- **D5 · A failed copy is not a failed backup.** The backup run stays succeeded. The page shows *Off-server copy: Failed* with the reason, and the alert says *"Backup not copied off the server … The backup on this server is fine."*
- **D6 · Done counts by itself.** The setup step "Backups copied off this server" is done once the copy is connected. The owner can still tick it by hand if they copy some other way.
- **The master key is still not in a copy.** The copy is the same directory as the backup on the server.

## 3. Not in this proposal

- `deploypro backup` on the command line writes where it is told, and does not copy.
- Restoring from the box: download the folder (SFTP, or Hetzner's own tools) and use `deploypro restore-volume` and `pg_restore`, as for a backup on the server.

## 4. Checked

The `sftp` client was run against a real OpenSSH server with a password-only account, standing in for a Storage Box. These all worked:
- the password installs the key, and installing again adds no second line
- a wrong password, and a changed host key, are refused with the sentences above
- four copies with keep 2 leave the newest two, including `volumes/`
- a retried copy replaces the old one, and the owner's own key line survives

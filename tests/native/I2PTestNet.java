/* One real router in a synthetic private network; never a public launcher. */
import java.io.ByteArrayInputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;
import net.i2p.router.Router;
import net.i2p.router.OutNetMessage;
import net.i2p.data.router.RouterInfo;
import net.i2p.data.i2np.DatabaseStoreMessage;

public final class I2PTestNet {
    public static void main(String[] args) throws Exception {
        if (args.length != 4) throw new IllegalArgumentException("private root, index and local ports required");
        Path root = Path.of(args[0]).toAbsolutePath();
        int index = Integer.parseInt(args[1]);
        Path dir = root.resolve("r" + index);
        Files.createDirectories(dir.resolve("tmp"));
        Files.writeString(dir.resolve("clients.config"), "# no apps or public services\n");
        Properties p = new Properties();
        p.setProperty("i2p.dir.base", dir.toString());
        p.setProperty("i2p.dir.config", dir.toString());
        p.setProperty("i2p.dir.temp", dir.resolve("tmp").toString());
        p.setProperty("router.configLocation", dir.resolve("router.config").toString());
        p.setProperty("i2cp.port", args[2]);
        p.setProperty("i2cp.hostname", "127.0.0.1");
        p.setProperty("i2np.ntcp.port", args[3]);
        p.setProperty("i2np.ntcp.hostname", "127.0.0.1");
        p.setProperty("i2np.ntcp.bindInterface", "127.0.0.1");
        p.setProperty("i2np.ntcp.autoip", "false");
        p.setProperty("i2np.ntcp.enable", "false");
        p.setProperty("i2np.udp.enable", "true");
        p.setProperty("i2np.udp.host", "127.0.0.1");
        p.setProperty("i2np.udp.bindInterface", "127.0.0.1");
        p.setProperty("i2np.udp.internalPort", args[3]);
        p.setProperty("i2np.udp.port", args[3]);
        p.setProperty("i2np.udp.ipv6", "false");
        p.setProperty("i2np.udp.status", "ok");
        p.setProperty("i2np.allowLocal", "true");
        p.setProperty("i2np.upnp.enable", "false");
        p.setProperty("router.networkID", "99");
        p.setProperty("router.reseedDisable", "true");
        p.setProperty("router.reseedURL", "");
        p.setProperty("router.newsRefreshFrequency", "0");
        p.setProperty("router.sybilFrequency", "0");
        p.setProperty("router.blocklist.enable", "false");
        p.setProperty("router.floodfillParticipant", "true");
        p.setProperty("router.rejectStartupTime", "0");
        p.setProperty("router.sharePercentage", "80");
        p.setProperty("time.disabled", "true");
        p.setProperty("time.sntpServerList", "localhost");
        p.setProperty("i2np.bandwidth.inboundKBytesPerSecond", "512");
        p.setProperty("i2np.bandwidth.outboundKBytesPerSecond", "512");
        p.setProperty("loggerFilenameOverride", dir.resolve("router.log").toString());
        try (java.io.OutputStream config = Files.newOutputStream(dir.resolve("router.config"))) {
            p.store(config, "synthetic private I2P test network");
        }
        Files.writeString(dir.resolve("logger.config"),
            "logger.defaultLevel=INFO\nlogger.logBufferSize=100\nlogger.flushInterval=1\nlogger.fileSize=2000000\nlogger.numFiles=2\n");
        Router router = new Router(p);
        router.setKillVMOnEnd(false);
        if (router.getNetworkID() != 99) throw new IllegalStateException("private network required");
        Thread eof = new Thread(() -> {
            try { while (System.in.read() != -1) { } } catch (Exception ignored) { }
            Runtime.getRuntime().halt(0);
        });
        eof.setDaemon(true);
        eof.start();
        Thread runner = new Thread(() -> router.runRouter());
        runner.setDaemon(true);
        runner.start();
        long deadline = System.nanoTime() + 600000000000L;
        while (System.nanoTime() < deadline) {
            if (router.getContext().netDb() != null && router.getContext().netDb().isInitialized()
                    && router.getRouterInfo() != null) {
                for (int other = 0; other < 8; other++) {
                    if (other == index) continue;
                    Path file = root.resolve("r" + other).resolve("router.info");
                    if (!Files.isRegularFile(file)) continue;
                    try {
                        RouterInfo info = new RouterInfo();
                        info.readBytes(new ByteArrayInputStream(Files.readAllBytes(file)));
                        if (info.getNetworkId() != 99) throw new IllegalStateException("public peer refused");
                        router.getContext().netDb().store(info.getIdentity().getHash(), info);
                        router.getContext().profileManager().heardAbout(info.getIdentity().getHash(), info.getCapabilities());
                        // Establish actual native transport sessions with native router messages.
                        if (index > other) continue;
                        DatabaseStoreMessage message = new DatabaseStoreMessage(router.getContext());
                        message.setEntry(router.getRouterInfo());
                        message.setMessageExpiration(router.getContext().clock().now() + 10000);
                        router.getContext().outNetMessagePool().add(new OutNetMessage(
                            router.getContext(), message, message.getMessageExpiration(), 100, info));
                    } catch (java.io.IOException ignored) { }
                }
                Files.writeString(root.resolve("state-" + index + ".txt"),
                    "ready=" + (router.getContext().throttle().acceptTunnelRequest() == 0)
                    + " caps=" + router.getRouterInfo().getCapabilities()
                    + " active=" + router.getContext().commSystem().countActivePeers()
                    + " fast=" + router.getContext().profileOrganizer().countFastPeers()
                    + " clientTunnels=" + router.getContext().tunnelManager().getInboundClientTunnelCount());
            }
            Thread.sleep(5000);
        }
        Runtime.getRuntime().halt(0);
    }
}

/* Opt-in, finite public-network qualification router; no apps or existing state. */
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;
import net.i2p.router.Router;

public final class I2PPublicRouter {
    public static void main(String[] args) throws Exception {
        if (args.length != 6 || !args[5].equals("allow-public-i2p"))
            throw new IllegalArgumentException("explicit public I2P grant required");
        Path dir = Path.of(args[0]).toAbsolutePath();
        int seconds = Integer.parseInt(args[3]);
        int kib = Integer.parseInt(args[4]);
        if (seconds < 180 || seconds > 1800 || kib < 16 || kib > 128)
            throw new IllegalArgumentException("qualification bounds exceeded");
        Files.createDirectories(dir.resolve("tmp"));
        Files.writeString(dir.resolve("clients.config"), "# no console, plugins or managed applications\n");
        Properties p = new Properties();
        p.setProperty("i2p.dir.base", dir.toString());
        p.setProperty("i2p.dir.config", dir.toString());
        p.setProperty("i2p.dir.temp", dir.resolve("tmp").toString());
        p.setProperty("router.configLocation", dir.resolve("router.config").toString());
        p.setProperty("i2cp.hostname", "127.0.0.1");
        p.setProperty("i2cp.port", args[1]);
        p.setProperty("i2np.ntcp.enable", "false");
        p.setProperty("i2np.udp.enable", "true");
        p.setProperty("i2np.udp.ipv6", "false");
        p.setProperty("i2np.udp.internalPort", args[2]);
        p.setProperty("i2np.udp.port", args[2]);
        p.setProperty("i2np.upnp.enable", "false");
        p.setProperty("router.networkID", "2");
        p.setProperty("router.floodfillParticipant", "false");
        p.setProperty("router.sharePercentage", "0");
        p.setProperty("router.newsRefreshFrequency", "0");
        p.setProperty("router.sybilFrequency", "0");
        p.setProperty("time.disabled", "true");
        p.setProperty("i2np.bandwidth.inboundKBytesPerSecond", Integer.toString(kib));
        p.setProperty("i2np.bandwidth.outboundKBytesPerSecond", Integer.toString(kib));
        p.setProperty("i2np.bandwidth.inboundBurstKBytesPerSecond", Integer.toString(kib));
        p.setProperty("i2np.bandwidth.outboundBurstKBytesPerSecond", Integer.toString(kib));
        p.setProperty("loggerFilenameOverride", dir.resolve("router.log").toString());
        Files.writeString(dir.resolve("logger.config"), "logger.defaultLevel=WARN\nlogger.fileSize=2000000\nlogger.numFiles=2\n");
        try (java.io.OutputStream stream = Files.newOutputStream(dir.resolve("router.config"))) {
            p.store(stream, "finite public qualification, zero configured transit share");
        }
        Thread eof = new Thread(() -> {
            try { while (System.in.read() != -1) { } } catch (Exception ignored) { }
            Runtime.getRuntime().halt(0);
        });
        eof.setDaemon(true);
        eof.start();
        Router router = new Router(p);
        router.setKillVMOnEnd(false);
        if (router.getNetworkID() != 2) throw new IllegalStateException("wrong qualification network");
        Thread run = new Thread(() -> router.runRouter());
        run.setDaemon(true);
        run.start();
        long deadline = System.nanoTime() + seconds * 1000000000L;
        while (System.nanoTime() < deadline) {
            if (router.getContext().commSystem() != null && router.getContext().bandwidthLimiter() != null)
                Files.writeString(dir.resolve("observation.json"),
                "{\"active_peers\":" + router.getContext().commSystem().countActivePeers()
                + ",\"allocated_inbound_bytes\":" + router.getContext().bandwidthLimiter().getTotalAllocatedInboundBytes()
                + ",\"allocated_outbound_bytes\":" + router.getContext().bandwidthLimiter().getTotalAllocatedOutboundBytes()
                + ",\"uptime_ms\":" + router.getContext().router().getUptime() + "}");
            Thread.sleep(2000);
        }
        Runtime.getRuntime().halt(0);
    }
}

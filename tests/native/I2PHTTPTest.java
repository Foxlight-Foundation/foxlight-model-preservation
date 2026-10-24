/* Stock I2P HTTP tunnels on an explicitly supplied private test router. */
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;
import net.i2p.client.I2PClientFactory;
import net.i2p.crypto.SigType;
import net.i2p.data.Destination;
import net.i2p.i2ptunnel.I2PTunnel;
import net.i2p.i2ptunnel.Logging;

public final class I2PHTTPTest {
    public static void main(String[] args) throws Exception {
        if (args.length != 4 && args.length != 5) throw new IllegalArgumentException("mode, private directory, I2CP and local port required");
        if (!args[0].equals("client") && !args[0].equals("server")) throw new IllegalArgumentException("invalid test mode");
        Path root = Path.of(args[1]).toAbsolutePath();
        Files.createDirectories(root);
        System.setProperty("i2p.dir.base", root.toString());
        System.setProperty("i2p.dir.config", root.toString());
        System.setProperty("i2p.dir.temp", root.toString());
        System.setProperty("loggerFilenameOverride", root.resolve("native.log").toString());
        Thread eof = new Thread(() -> {
            try { while (System.in.read() != -1) { } } catch (Exception ignored) { }
            Runtime.getRuntime().halt(0);
        });
        eof.setDaemon(true);
        eof.start();
        I2PTunnel tunnel = new I2PTunnel();
        tunnel.host = "127.0.0.1";
        tunnel.port = args[2];
        tunnel.listenHost = "127.0.0.1";
        Properties options = tunnel.getClientOptions();
        options.setProperty("inbound.length", "3");
        options.setProperty("outbound.length", "3");
        options.setProperty("inbound.lengthVariance", "0");
        options.setProperty("outbound.lengthVariance", "0");
        options.setProperty("i2ptunnel.useLocalOutproxy", "false");
        options.setProperty("outproxyPlugin", "false");
        options.setProperty("proxyList", "");
        options.setProperty("jumpServers", "");
        options.setProperty("i2cp.closeOnIdle", "false");
        options.setProperty("i2cp.reduceOnIdle", "false");
        Logging log = System.out::println;
        if (args[0].equals("server")) {
            Path key = root.resolve("synthetic-destination.key");
            Destination destination;
            try (java.io.OutputStream stream = Files.newOutputStream(key)) {
                destination = I2PClientFactory.createClient().createDestination(stream, SigType.EdDSA_SHA512_Ed25519);
            }
            tunnel.runHttpServer(new String[] {"127.0.0.1", args[3], "fixture.i2p", key.toString()}, log);
            Files.writeString(root.resolve("destination.txt"), destination.toBase32());
        } else {
            tunnel.runHttpClient(new String[] {args[3], "false"}, log);
        }
        if (tunnel.getSessions().isEmpty()) throw new IllegalStateException("native tunnel has no I2P session");
        Files.writeString(root.resolve("ready.txt"), "inbound=3 outbound=3 localOnly=true noOutproxy=true");
        int seconds = args.length == 5 ? Integer.parseInt(args[4]) : 300;
        if (seconds < 30 || seconds > 1800) throw new IllegalArgumentException("invalid finite helper lifetime");
        Thread.sleep(seconds * 1000L);
        Runtime.getRuntime().halt(0);
    }
}

/* An owned, finite launcher for the independently selected I2PSnark runtime.
 * SPDX-License-Identifier: MIT. See SnarkPilot-LICENSE.txt.
 * Native I2P/Snark/RPC components retain their upstream licenses.
 */
package ai.foxlight.sovereign.i2p;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.Properties;
import javax.servlet.ServletException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import net.i2p.I2PAppContext;
import net.i2p.data.Destination;
import net.i2p.client.streaming.I2PSocketManager;
import org.eclipse.jetty.server.Server;
import org.eclipse.jetty.server.ServerConnector;
import org.eclipse.jetty.ee8.servlet.ServletContextHandler;
import org.eclipse.jetty.ee8.servlet.ServletHolder;
import org.klomp.snark.SnarkManager;
import org.klomp.snark.dht.DHT;
import org.klomp.snark.rpc.RPCServlet;

/** No router installation, browser, public socket or deletion API is added. */
public final class SnarkPilot {
    private static final class AuthRPC extends RPCServlet {
        private final byte[] authorization;

        AuthRPC(String token) {
            authorization = ("Bearer " + token).getBytes(StandardCharsets.US_ASCII);
        }

        @Override
        protected void service(HttpServletRequest request, HttpServletResponse response)
                throws ServletException, IOException {
            String supplied = request.getHeader("Authorization");
            if (supplied == null || !MessageDigest.isEqual(authorization,
                    supplied.getBytes(StandardCharsets.US_ASCII))) {
                response.sendError(401);
                return;
            }
            super.service(request, response);
        }
    }

    private static void stop() {
        // Bound even a native shutdown hook that stalls after controller loss.
        Thread hardStop = new Thread(() -> {
            try { Thread.sleep(4000); } catch (InterruptedException ignored) { }
            Runtime.getRuntime().halt(0);
        }, "sovereign-i2p-hard-stop");
        hardStop.setDaemon(true);
        hardStop.start();
        System.exit(0);
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) throw new IllegalArgumentException("finite lifetime required");
        int seconds = Integer.parseInt(args[0]);
        if (seconds < 1 || seconds > 86400 || !args[1].equals("i2p-payload-sharing"))
            throw new IllegalArgumentException("explicit finite sharing grant required");
        long deadline = System.nanoTime() + seconds * 1000000000L;
        Thread fence = new Thread(() -> {
            try { while (System.in.read() != -1) { } } catch (IOException ignored) { }
            stop();
        }, "sovereign-i2p-eof");
        fence.setDaemon(true);
        fence.start();
        Thread timer = new Thread(() -> {
            while (System.nanoTime() < deadline) {
                try { Thread.sleep(100); } catch (InterruptedException ignored) { }
            }
            stop();
        }, "sovereign-i2p-deadline");
        timer.setDaemon(true);
        timer.start();

        Path root = Path.of(".").toAbsolutePath().normalize();
        String token = Files.readString(root.resolve("control.token")).trim();
        if (!token.matches("[0-9a-f]{64}")) throw new IOException("invalid control token");
        Properties properties = new Properties();
        properties.setProperty("i2p.dir.base", root.toString());
        properties.setProperty("i2p.dir.config", root.toString());
        properties.setProperty("i2p.dir.temp", root.resolve("tmp").toString());
        properties.setProperty("time.disabled", "true");
        I2PAppContext context = new I2PAppContext(properties);
        SnarkManager manager = new SnarkManager(context);
        manager.loadConfig("i2psnark.config");
        manager.start();
        Server server = new Server();
        ServerConnector connector = new ServerConnector(server);
        connector.setHost("127.0.0.1");
        connector.setPort(0);
        server.addConnector(connector);
        ServletContextHandler handler = new ServletContextHandler();
        handler.setContextPath("/transmission");
        handler.addServlet(new ServletHolder(new AuthRPC(token)), "/rpc");
        server.setHandler(handler);
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            manager.stop();
            try { server.stop(); } catch (Exception ignored) { }
        }, "sovereign-i2p-owned-shutdown"));
        server.start();
        long nextBootstrap = 0;
        while (System.nanoTime() < deadline) {
            DHT dht = manager.util().getDHT();
            String destination = manager.util().getOurIPString();
            I2PSocketManager sockets = manager.util().getSocketManager();
            boolean connected = dht != null && destination != null && sockets != null
                && sockets.getSession() != null && !sockets.getSession().isClosed();
            if (connected && dht.size() == 0 && System.nanoTime() >= nextBootstrap) {
                for (String line : Files.readAllLines(root.resolve("contacts.txt"))) {
                    if (line.isEmpty()) continue;
                    int split = line.lastIndexOf(':');
                    Destination contact = new Destination(line.substring(0, split));
                    dht.ping(contact, Integer.parseInt(line.substring(split + 1)));
                }
                nextBootstrap = System.nanoTime() + 5000000000L;
            }
            String status = "{\"rpc_port\":" + connector.getLocalPort()
                + ",\"pid\":" + ProcessHandle.current().pid()
                + ",\"connected\":" + connected
                + ",\"dht_nodes\":" + (dht == null ? 0 : dht.size())
                + ",\"destination\":" + (connected ? "\"" + destination + "\"" : "null")
                + ",\"dht_port\":" + (dht == null ? 0 : dht.getPort()) + "}";
            Path pending = root.resolve("status.pending");
            Files.writeString(pending, status);
            Files.move(pending, root.resolve("status.json"), StandardCopyOption.REPLACE_EXISTING,
                       StandardCopyOption.ATOMIC_MOVE);
            Thread.sleep(200);
        }
        stop();
    }
}

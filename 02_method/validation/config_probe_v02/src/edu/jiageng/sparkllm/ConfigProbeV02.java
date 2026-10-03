package edu.jiageng.sparkllm;

import java.io.OutputStreamWriter;
import java.io.Writer;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

import org.apache.hadoop.fs.FSDataOutputStream;
import org.apache.hadoop.fs.FileSystem;
import org.apache.hadoop.fs.Path;
import org.apache.spark.SparkConf;
import org.apache.spark.SparkExecutorInfo;
import org.apache.spark.api.java.JavaRDD;
import org.apache.spark.api.java.JavaSparkContext;
import org.apache.spark.SparkStatusTracker;
import org.apache.spark.api.java.function.Function;
import org.apache.spark.api.java.function.Function2;

import scala.Tuple2;

/**
 * ConfigProbe V0.2
 *
 * V0.1-compatible measurement probe with extra fixture modes:
 *
 * basic
 *   Same tiny action as V0.1.
 *
 * hold <seconds>
 *   Run tiny action, then keep the Spark application alive for runtime
 *   observation (e.g. YARN container resources).
 *
 * driver-late-conf <memory>
 *   Fixture mode only: set spark.driver.memory on the application SparkConf
 *   immediately before SparkContext creation. This deliberately represents
 *   the real-world "set driver memory inside the application in client mode"
 *   placement mistake. Runtime.maxMemory() is recorded separately so the
 *   experiment can distinguish an effective-conf string from actual JVM heap.
 *
 * dynamic
 *   Generate backlog with many sleeping tasks, sample executor counts while
 *   the job is active, then wait through an idle window. Used only to validate
 *   dynamic-allocation runtime behavior.
 *
 * observe <seconds>
 *   Create SparkContext and observe executor counts without workload.
 *   Useful for initial-executor semantics.
 *
 * This remains a measurement application. It does not decide PASS/FAIL.
 */
public final class ConfigProbeV02 {

    public static final String PROBE_VERSION = "0.2";
    public static final String SCHEMA_VERSION = "2";

    private ConfigProbeV02() {}

    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println(
                "Usage: ConfigProbeV02 <hdfs-output-dir> <basic|hold|driver-late-conf|dynamic|observe> [mode-arg]"
            );
            System.exit(2);
        }

        final String outputDir = args[0];
        final String mode = args[1];
        SparkConf conf = new SparkConf();

        String fixtureDriverMemory = null;
        int holdSeconds = 0;
        int observeSeconds = 0;

        if ("driver-late-conf".equals(mode)) {
            if (args.length != 3) {
                throw new IllegalArgumentException("driver-late-conf requires memory value");
            }
            fixtureDriverMemory = args[2];
            // Deliberate task fixture: this is NOT parser repair and NOT a
            // benchmark answer. It represents an application-side SparkConf.
            conf.set("spark.driver.memory", fixtureDriverMemory);
        } else if ("hold".equals(mode)) {
            if (args.length != 3) {
                throw new IllegalArgumentException("hold requires seconds");
            }
            holdSeconds = Integer.parseInt(args[2]);
        } else if ("observe".equals(mode)) {
            if (args.length != 3) {
                throw new IllegalArgumentException("observe requires seconds");
            }
            observeSeconds = Integer.parseInt(args[2]);
        } else if (!"basic".equals(mode) && !"dynamic".equals(mode)) {
            throw new IllegalArgumentException("Unknown mode: " + mode);
        }

        JavaSparkContext jsc = null;
        try {
            jsc = new JavaSparkContext(conf);

            String appId = jsc.sc().applicationId();
            String sparkVersion = jsc.sc().version();
            String master = jsc.sc().master();
            String host = InetAddress.getLocalHost().getHostName();

            long driverMaxMemoryBytes = Runtime.getRuntime().maxMemory();
            double driverMaxMemoryMiB = driverMaxMemoryBytes / 1024.0 / 1024.0;

            Integer actionResult = 0;
            Integer dynamicPeakNonDriver = null;
            Integer dynamicAfterIdleNonDriver = null;
            List<Integer> dynamicSeenCounts = new ArrayList<Integer>();
            Integer observedMaxNonDriver = null;
            Integer observedMinNonDriver = null;
            List<Integer> observedCounts = new ArrayList<Integer>();

            if ("basic".equals(mode) || "hold".equals(mode) || "driver-late-conf".equals(mode)) {
                actionResult = tinyAction(jsc);
                if ("hold".equals(mode) && holdSeconds > 0) {
                    Thread.sleep(holdSeconds * 1000L);
                }
            } else if ("dynamic".equals(mode)) {
                DynamicMetrics dm = runDynamicFixture(jsc);
                actionResult = dm.actionResult;
                dynamicPeakNonDriver = dm.peak;
                dynamicAfterIdleNonDriver = dm.afterIdle;
                dynamicSeenCounts.addAll(dm.seen);
            } else if ("observe".equals(mode)) {
                SparkStatusTracker tracker = jsc.sc().statusTracker();
                long deadline = System.currentTimeMillis() + observeSeconds * 1000L;
                while (System.currentTimeMillis() < deadline) {
                    int count = nonDriverExecutorCount(tracker);
                    observedCounts.add(count);
                    if (observedMaxNonDriver == null || count > observedMaxNonDriver) {
                        observedMaxNonDriver = count;
                    }
                    if (observedMinNonDriver == null || count < observedMinNonDriver) {
                        observedMinNonDriver = count;
                    }
                    Thread.sleep(500L);
                }
                actionResult = tinyAction(jsc);
            }

            Tuple2<String, String>[] all = jsc.getConf().getAll();
            Arrays.sort(all, new Comparator<Tuple2<String, String>>() {
                @Override
                public int compare(Tuple2<String, String> a, Tuple2<String, String> b) {
                    return a._1().compareTo(b._1());
                }
            });

            String json = buildJson(
                appId, sparkVersion, master, host, outputDir, mode,
                fixtureDriverMemory, driverMaxMemoryBytes, driverMaxMemoryMiB,
                actionResult, dynamicPeakNonDriver, dynamicAfterIdleNonDriver,
                dynamicSeenCounts, observedMaxNonDriver, observedMinNonDriver,
                observedCounts, all
            );

            Path dir = new Path(outputDir);
            FileSystem fs = dir.getFileSystem(jsc.hadoopConfiguration());
            if (!fs.exists(dir) && !fs.mkdirs(dir)) {
                throw new IllegalStateException("Cannot create HDFS output directory: " + outputDir);
            }

            Path file = new Path(dir, "effective_conf.json");
            try (FSDataOutputStream out = fs.create(file, true);
                 Writer writer = new OutputStreamWriter(out, StandardCharsets.UTF_8)) {
                writer.write(json);
                writer.write("\n");
            }

            System.out.println("CONFIG_PROBE_APP_ID=" + appId);
            System.out.println("CONFIG_PROBE_OUTPUT=" + file.toString());
            System.out.println("CONFIG_PROBE_MODE=" + mode);
            System.out.println("CONFIG_PROBE_ACTION_RESULT=" + actionResult);
            System.out.println("CONFIG_PROBE_DRIVER_MAX_MEMORY_MIB=" + driverMaxMemoryMiB);
            if (dynamicPeakNonDriver != null) {
                System.out.println("CONFIG_PROBE_DYNAMIC_PEAK=" + dynamicPeakNonDriver);
                System.out.println("CONFIG_PROBE_DYNAMIC_AFTER_IDLE=" + dynamicAfterIdleNonDriver);
            }
            if (observedMaxNonDriver != null) {
                System.out.println("CONFIG_PROBE_OBSERVED_MAX_EXECUTORS=" + observedMaxNonDriver);
            }
        } finally {
            if (jsc != null) {
                jsc.stop();
            }
        }
    }

    private static Integer tinyAction(JavaSparkContext jsc) {
        return jsc.parallelize(Arrays.asList(1, 2, 3, 4), 2)
            .reduce(new Function2<Integer, Integer, Integer>() {
                private static final long serialVersionUID = 1L;
                @Override
                public Integer call(Integer a, Integer b) {
                    return a + b;
                }
            });
    }

    private static int nonDriverExecutorCount(SparkStatusTracker tracker) {
        SparkExecutorInfo[] infos = tracker.getExecutorInfos();
        // SparkStatusTracker includes the driver.
        return Math.max(0, infos.length - 1);
    }

    private static DynamicMetrics runDynamicFixture(final JavaSparkContext jsc) throws Exception {
        final SparkStatusTracker tracker = jsc.sc().statusTracker();
        final int partitions = 60;

        List<Integer> input = new ArrayList<Integer>();
        for (int i = 0; i < partitions; i++) {
            input.add(i);
        }

        final JavaRDD<Integer> rdd = jsc.parallelize(input, partitions)
            .map(new SleepOneFunction());

        final int[] resultHolder = new int[] {0};
        final Throwable[] errorHolder = new Throwable[] {null};

        Thread actionThread = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    resultHolder[0] = rdd.reduce(new SumIntFunction());
                } catch (Throwable t) {
                    errorHolder[0] = t;
                }
            }
        }, "config-probe-dynamic-action");

        List<Integer> seen = new ArrayList<Integer>();
        int peak = 0;
        actionThread.start();

        while (actionThread.isAlive()) {
            int count = nonDriverExecutorCount(tracker);
            seen.add(count);
            peak = Math.max(peak, count);
            Thread.sleep(500L);
        }
        actionThread.join();

        if (errorHolder[0] != null) {
            throw new RuntimeException("Dynamic fixture action failed", errorHolder[0]);
        }

        // The validation fixture configures executorIdleTimeout=5s. Wait long
        // enough to observe scale-down after the backlog disappears.
        long idleDeadline = System.currentTimeMillis() + 12000L;
        int afterIdle = nonDriverExecutorCount(tracker);
        while (System.currentTimeMillis() < idleDeadline) {
            afterIdle = nonDriverExecutorCount(tracker);
            seen.add(afterIdle);
            Thread.sleep(500L);
        }

        return new DynamicMetrics(resultHolder[0], peak, afterIdle, seen);
    }

    private static final class SleepOneFunction implements Function<Integer, Integer> {
        private static final long serialVersionUID = 1L;

        @Override
        public Integer call(Integer x) throws Exception {
            Thread.sleep(500L);
            return 1;
        }
    }

    private static final class SumIntFunction implements Function2<Integer, Integer, Integer> {
        private static final long serialVersionUID = 1L;

        @Override
        public Integer call(Integer a, Integer b) {
            return a + b;
        }
    }

    private static final class DynamicMetrics {
        final int actionResult;
        final int peak;
        final int afterIdle;
        final List<Integer> seen;

        DynamicMetrics(int actionResult, int peak, int afterIdle, List<Integer> seen) {
            this.actionResult = actionResult;
            this.peak = peak;
            this.afterIdle = afterIdle;
            this.seen = seen;
        }
    }

    private static String buildJson(
        String appId,
        String sparkVersion,
        String master,
        String host,
        String outputDir,
        String mode,
        String fixtureDriverMemory,
        long driverMaxMemoryBytes,
        double driverMaxMemoryMiB,
        Integer actionResult,
        Integer dynamicPeakNonDriver,
        Integer dynamicAfterIdleNonDriver,
        List<Integer> dynamicSeenCounts,
        Integer observedMaxNonDriver,
        Integer observedMinNonDriver,
        List<Integer> observedCounts,
        Tuple2<String, String>[] conf
    ) {
        StringBuilder sb = new StringBuilder(65536);
        sb.append("{\n");
        field(sb, "schema_version", SCHEMA_VERSION, true);
        field(sb, "probe_version", PROBE_VERSION, true);
        field(sb, "timestamp_utc", Instant.now().toString(), true);
        field(sb, "application_id", appId, true);
        field(sb, "spark_version", sparkVersion, true);
        field(sb, "master", master, true);
        field(sb, "driver_host", host, true);
        field(sb, "probe_output_dir", outputDir, true);
        field(sb, "mode", mode, true);

        if (fixtureDriverMemory == null) {
            sb.append("  \"fixture_driver_memory\": null,\n");
        } else {
            field(sb, "fixture_driver_memory", fixtureDriverMemory, true);
        }

        sb.append("  \"driver_max_memory_bytes\": ").append(driverMaxMemoryBytes).append(",\n");
        sb.append("  \"driver_max_memory_mib\": ").append(String.format(java.util.Locale.US, "%.3f", driverMaxMemoryMiB)).append(",\n");
        sb.append("  \"action_result\": ").append(actionResult).append(",\n");

        nullableInt(sb, "dynamic_peak_non_driver_executors", dynamicPeakNonDriver, true);
        nullableInt(sb, "dynamic_after_idle_non_driver_executors", dynamicAfterIdleNonDriver, true);
        intArray(sb, "dynamic_seen_non_driver_executor_counts", dynamicSeenCounts, true);

        nullableInt(sb, "observed_max_non_driver_executors", observedMaxNonDriver, true);
        nullableInt(sb, "observed_min_non_driver_executors", observedMinNonDriver, true);
        intArray(sb, "observed_non_driver_executor_counts", observedCounts, true);

        sb.append("  \"effective_conf\": {\n");
        for (int i = 0; i < conf.length; i++) {
            sb.append("    \"").append(escape(conf[i]._1())).append("\": \"")
              .append(escape(conf[i]._2())).append("\"");
            if (i + 1 < conf.length) sb.append(",");
            sb.append("\n");
        }
        sb.append("  }\n");
        sb.append("}");
        return sb.toString();
    }

    private static void field(StringBuilder sb, String key, String value, boolean comma) {
        sb.append("  \"").append(escape(key)).append("\": \"")
          .append(escape(value)).append("\"");
        if (comma) sb.append(",");
        sb.append("\n");
    }

    private static void nullableInt(StringBuilder sb, String key, Integer value, boolean comma) {
        sb.append("  \"").append(escape(key)).append("\": ");
        if (value == null) sb.append("null");
        else sb.append(value);
        if (comma) sb.append(",");
        sb.append("\n");
    }

    private static void intArray(StringBuilder sb, String key, List<Integer> values, boolean comma) {
        sb.append("  \"").append(escape(key)).append("\": [");
        for (int i = 0; i < values.size(); i++) {
            if (i > 0) sb.append(", ");
            sb.append(values.get(i));
        }
        sb.append("]");
        if (comma) sb.append(",");
        sb.append("\n");
    }

    private static String escape(String s) {
        if (s == null) return "";
        StringBuilder out = new StringBuilder(s.length() + 16);
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\b': out.append("\\b"); break;
                case '\f': out.append("\\f"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) out.append(String.format("\\u%04x", (int)c));
                    else out.append(c);
            }
        }
        return out.toString();
    }
}

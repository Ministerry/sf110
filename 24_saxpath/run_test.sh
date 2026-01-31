set -euo pipefail
# optional: override JAVA_HOME before calling script
export JAVA_HOME=/home/ubuntu/.conda/envs/rmy_llama/lib/jvm
STANDALONE=/home/ubuntu/myren/SF110/lib/evosuite-standalone-runtime-1.1.0.jar
EVOJAR=/home/ubuntu/myren/SF110/lib/evosuite.jar
JUNIT4_JAR=/home/ubuntu/myren/SF110/lib/junit-4.13.2.jar
HAMCREST_JAR=/home/ubuntu/myren/SF110/lib/hamcrest-core-1.3.jar
PROJECT_TEST_LIB_DIR="$(pwd)/test-lib"
PROJECT_LIB_DIR="$(pwd)/lib"
EXTRA_JARS=""
if [ -d "$PROJECT_TEST_LIB_DIR" ]; then
  for jar in "$PROJECT_TEST_LIB_DIR"/*.jar; do
    [ -f "$jar" ] && EXTRA_JARS="${EXTRA_JARS}:$jar"
  done
fi
PROJECT_JARS=""
if [ -d "$PROJECT_LIB_DIR" ]; then
  for jar in "$PROJECT_LIB_DIR"/*.jar; do
    [ -f "$jar" ] && PROJECT_JARS="${PROJECT_JARS}:$jar"
  done
fi
if [ -d "/usr/share/ant/lib" ]; then
  for jar in /usr/share/ant/lib/*.jar; do
    [ -f "$jar" ] && PROJECT_JARS="${PROJECT_JARS}:$jar"
  done
fi
CP="${EVOJAR}:${JUNIT4_JAR}:${HAMCREST_JAR}${EXTRA_JARS}${PROJECT_JARS}:."

# choose java binary
if [ -n "$JAVA_HOME" ] && [ -x "$JAVA_HOME/bin/java" ]; then
  JAVA_BIN="$JAVA_HOME/bin/java"
else
  JAVA_BIN="$(which java 2>/dev/null || echo java)"
fi

"$JAVA_BIN" -version 2>&1 | sed -n '1,3p'
TEST_CLASS="$1"
CP_RUN="target/classes:target/test-classes:$CP"

# Allow caller to set JAVA_OPTS. Default to UTF-8 first.
JAVA_OPTS=${JAVA_OPTS:-"-Dfile.encoding=UTF-8"}
# JaCoCo paths under target/
TARGET_DIR="$(pwd)/report"
JACOCO_DIR="${TARGET_DIR}/jacoco"
JACOCO_REPORT_DIR="${TARGET_DIR}/jacoco-report"
mkdir -p "${JACOCO_DIR}" "${JACOCO_REPORT_DIR}"

# JaCoCo agent
JACOCO_AGENT_JAR=/home/ubuntu/myren/jacoco/lib/jacocoagent.jar
JACOCO_EXEC=${JACOCO_EXEC:-${JACOCO_DIR}/coverage.exec}
JAVA_OPTS="${JAVA_OPTS} -javaagent:${JACOCO_AGENT_JAR}=destfile=${JACOCO_EXEC},append=true"

# JaCoCo report config
JACOCO_CLI_JAR=/home/ubuntu/myren/jacoco/lib/jacococli.jar
generate_report() {
  if [ -f "$JACOCO_EXEC" ] && [ -f "$JACOCO_CLI_JAR" ]; then
    echo "Generating JaCoCo report -> ${JACOCO_REPORT_DIR}"
    "$JAVA_BIN" -jar "$JACOCO_CLI_JAR" report "$JACOCO_EXEC" \
      --classfiles "target/classes" \
      --classfiles "target/test-classes" \
      --sourcefiles "src/main/java" \
      --html "$JACOCO_REPORT_DIR" \
      --xml  "$JACOCO_REPORT_DIR/coverage.xml"
  else
    echo "Skip JaCoCo report (missing exec or cli jar)"
  fi
}

echo "Running tests with JAVA_OPTS=\"$JAVA_OPTS\""
if "$JAVA_BIN" $JAVA_OPTS -cp "$CP_RUN" org.junit.runner.JUnitCore "$TEST_CLASS"; then
  generate_report
  exit 0
fi

echo "Test run failed with initial encoding. Retrying with ISO-8859-1..."
# ensure JaCoCo agent attached on retry
if "$JAVA_BIN" -Dfile.encoding=ISO-8859-1 -javaagent:${JACOCO_AGENT_JAR}=destfile=${JACOCO_EXEC},append=true -cp "$CP_RUN" org.junit.runner.JUnitCore "$TEST_CLASS"; then
  generate_report
  exit 0
fi

echo "Test run failed with both encodings."
exit 1

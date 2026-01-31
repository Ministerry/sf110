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

echo "Running tests with JAVA_OPTS=\"$JAVA_OPTS\""
if "$JAVA_BIN" $JAVA_OPTS -cp "$CP_RUN" org.junit.runner.JUnitCore "$TEST_CLASS"; then
  exit 0
fi

echo "Test run failed with initial encoding. Retrying with ISO-8859-1..."
if "$JAVA_BIN" -Dfile.encoding=ISO-8859-1 -cp "$CP_RUN" org.junit.runner.JUnitCore "$TEST_CLASS"; then
  exit 0
fi

echo "Test run failed with both encodings."
exit 1

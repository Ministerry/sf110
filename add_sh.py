import os

# 要遍历的顶层目录
base_dir = '/home/ubuntu/myren/SF110'  # 替换为你的路径
sh_filename = 'run_test.sh'
compile_filename = 'compile.sh'
run_filename = 'run.sh'
# 目标 shell 脚本内容
sh_compile = r'''#!/bin/bash
export JAVA_HOME=/home/ubuntu/.conda/envs/rmy_llama/lib/jvm
RUNNER_JAR="/home/ubuntu/myren/SF110/lib/evosuite.jar"
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

# Include project's own lib directory for JOX library
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

CP="${RUNNER_JAR}:${JUNIT4_JAR}:${HAMCREST_JAR}${EXTRA_JARS}${PROJECT_JARS}:."

mkdir -p target/classes
mkdir -p target/test-classes

# helper: try UTF-8 first, on failure retry with ISO-8859-1
# supports fallback to full compilation if incremental compilation fails
compile_with_fallback() {
  local dest="$1"; shift
  local cp_arg="$1"; shift
  local sources="$*"
  if [ -z "$sources" ]; then
    return 0
  fi
  
  # Try incremental compilation with sourcepath to find dependencies
  # -sourcepath src/main/java allows javac to find and compile missing dependencies automatically
  echo "Compiling to $dest with UTF-8..."
  javac -encoding UTF-8 -d "$dest" -cp "$cp_arg" -sourcepath src/main/java $sources 2>/tmp/javac_err.log
  rc=$?
  
  if [ $rc -ne 0 ]; then
    # If UTf-8 failed, check if we should retry with ISO-8859-1 OR fall back to full compile
    if grep -q "unmappable character" /tmp/javac_err.log; then
       echo "UTF-8 compile failed (encoding issue). Retrying with ISO-8859-1..."
       javac -encoding ISO-8859-1 -d "$dest" -cp "$cp_arg" -sourcepath src/main/java $sources
       rc2=$?
       if [ $rc2 -ne 0 ]; then
         cat /tmp/javac_err.log # Show original errors too
         echo "Compile failed with both encodings."
         return $rc2
       fi
    else
       # It might be a missing symbol error due to incremental compilation limitations
       # If we are doing incremental (sources is not empty but not all), try full clean build
       echo "Incremental compilation failed. Falling back to clean build..."
       cat /tmp/javac_err.log
       return $rc # Return error to trigger full rebuild in main logic
    fi
  else
     rm -f /tmp/javac_err.log
  fi
  return 0
}

# Helper: get incrementally changed files (only recompile if .java is newer than .class)
get_changed_sources() {
  local src_dir="$1"
  local dest_dir="$2"
  local changed=""
  
  if [ ! -d "$src_dir" ]; then
    return 0
  fi
  
  while IFS= read -r java_file; do
    # Convert source path to class path
    # e.g., src/main/java/com/example/Foo.java -> target/classes/com/example/Foo.class
    class_file="$dest_dir/$(echo "$java_file" | sed "s|^$src_dir/||" | sed 's/\.java$/.class/')"
    
    # Include if class doesn't exist or source is newer
    if [ ! -f "$class_file" ] || [ "$java_file" -nt "$class_file" ]; then
       changed="$changed $java_file"
    fi
  done < <(find "$src_dir" -name "*.java" 2>/dev/null)
  
  echo "$changed"
}

# Compile main source files (incremental with fallback)
MAIN_SOURCES=$(get_changed_sources "src/main/java" "target/classes")
# MAIN_SOURCES=$(find src/main/java -name "*.java")
if [ -n "$MAIN_SOURCES" ]; then
    echo "Incremental compile: $(echo $MAIN_SOURCES | wc -w) main source files changed"
    compile_with_fallback target/classes "$CP" $MAIN_SOURCES
    rc=$?
    if [ $rc -ne 0 ]; then
        echo "Incremental compilation failed. Cleaning and retrying full compilation..."
        rm -rf target/classes/*
        ALL_SOURCES=$(find src/main/java -name "*.java" 2>/dev/null)
        compile_with_fallback target/classes "$CP" $ALL_SOURCES
        rc=$?
        if [ $rc -ne 0 ]; then
             echo "Full compilation failed. Aborting."
             exit $rc
        fi
    fi
else
    echo "Main sources: no changes detected, skipping"
fi

# Compile evosuite test files if they exist (incremental)
EVOSUITE_SOURCES=$(get_changed_sources "evosuite-tests" "target/test-classes")
# EVOSUITE_SOURCES=$(find evosuite-tests -name "*.java")
if [ -n "$EVOSUITE_SOURCES" ]; then
    echo "Incremental compile: $(echo $EVOSUITE_SOURCES | wc -w) evosuite test files changed"
    compile_with_fallback target/test-classes "$CP:target/classes:target/test-classes" $EVOSUITE_SOURCES
fi

# Compile regular test files if they exist (incremental)
# TEST_SOURCES=$(get_changed_sources "src/test/java" "target/test-classes")
# if [ -n "$TEST_SOURCES" ]; then
#     echo "Incremental compile: $(echo $TEST_SOURCES | wc -w) regular test files changed"
#     compile_with_fallback target/test-classes "$CP:target/classes" $TEST_SOURCES
# fi

'''
sh_run_test = r'''set -euo pipefail
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
'''
sh_run = r'''set -euo pipefail
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
'''
for folder in os.listdir(base_dir):
    folder_path = os.path.join(base_dir, folder)
    if os.path.isdir(folder_path):
        sh_path = os.path.join(folder_path, sh_filename)
        if  os.path.exists(sh_path):
            with open(sh_path, 'w') as f:
                f.write(sh_run_test)
            os.chmod(sh_path, 0o755)  # 设置可执行权限
            print(f"✅ Created {sh_filename} in: {folder_path}")
        else:
            print(f"✔️ Exists: {sh_path}")

for folder in os.listdir(base_dir):
    folder_path = os.path.join(base_dir, folder)
    if os.path.isdir(folder_path):
        sh_path = os.path.join(folder_path, compile_filename)
        if  os.path.exists(sh_path):
            with open(sh_path, 'w') as f:
                f.write(sh_compile)
            os.chmod(sh_path, 0o755)  # 设置可执行权限
            print(f"✅ Created {compile_filename} in: {folder_path}")
        else:
            print(f"✔️ Exists: {sh_path}")

for folder in os.listdir(base_dir):
    folder_path = os.path.join(base_dir, folder)
    if os.path.isdir(folder_path):
        sh_path = os.path.join(folder_path, run_filename)
        if  os.path.exists(sh_path):
            with open(sh_path, 'w') as f:
                f.write(sh_run)
            os.chmod(sh_path, 0o755)  # 设置可执行权限
            print(f"✅ Created {run_filename} in: {folder_path}")
        else:
            print(f"✔️ Exists: {sh_path}")
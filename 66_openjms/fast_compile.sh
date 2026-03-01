#!/bin/bash
export JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64
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

mkdir -p target/classes
mkdir -p target/test-classes

JAVAC_FAST_OPTS="-g:lines -nowarn -proc:none"

# [重要] 必须将 target/classes 加入 Classpath
# 因为我们默认去掉了 sourcepath，javac 需要从这里查找现有的 .class 文件
CP="${RUNNER_JAR}:${JUNIT4_JAR}:${HAMCREST_JAR}${EXTRA_JARS}${PROJECT_JARS}:.:target/classes"

# helper: Multi-stage compilation strategy
# 1. Fast Path (UTF-8, No Sourcepath) - Fastest, works 99% of time for incremental changes
# 2. Symbol Fallback (UTF-8, With Sourcepath) - Retries if symbols are missing
# 3. Encoding Fallback (ISO-8859-1) - Retries if encoding fails
compile_with_fallback() {
  local dest="$1"; shift
  local cp_arg="$1"; shift
  local sources="$*"
  if [ -z "$sources" ]; then
    return 0
  fi

  local err_log
  err_log="$(mktemp /tmp/javac_err.XXXXXX.log)"

  # --- Stage 1: Fast Path ---
  # Only compile changed files, rely on CP for dependencies. Triggers minimal I/O.
  # echo "  [Fast Path] Compiling..."
  javac $JAVAC_FAST_OPTS -encoding UTF-8 -d "$dest" -cp "$cp_arg" $sources 2>"$err_log"
  rc=$?

  # --- Stage 2: Missing Symbols Fallback ---
  if [ $rc -ne 0 ]; then
    # If fast path failed due to missing symbols, we must enable sourcepath scanning
    if grep -qE "cannot find symbol|package .* does not exist" "$err_log"; then
      echo "  [Retry] Missing symbols in fast path, retrying with sourcepath..."
      javac $JAVAC_FAST_OPTS -encoding UTF-8 -d "$dest" -cp "$cp_arg" -sourcepath src/main/java $sources 2>"$err_log"
      rc=$?
    fi
  fi

  # --- Stage 3: Encoding Fallback ---
  if [ $rc -ne 0 ]; then
    if grep -q "unmappable character" "$err_log"; then
       echo "  [Retry] Encoding issue, retrying with ISO-8859-1..."
       # Keep sourcepath here just in case, but usually not needed if Stage 1 failed solely on encoding
       javac $JAVAC_FAST_OPTS -encoding ISO-8859-1 -d "$dest" -cp "$cp_arg" -sourcepath src/main/java $sources 2>"$err_log"
       rc2=$?
       if [ $rc2 -ne 0 ]; then
         cat "$err_log"
         echo "Compile failed with both encodings."
         rm -f "$err_log"
         return $rc2
       fi
       rc=0
    else
       # If it's not symbols and not encoding, it's a real syntax error or other fatal issue
       # But allow the main logic to decide if it wants to try a full clean build
       echo "Incremental compilation failed. Reason:"
       cat "$err_log"
       rm -f "$err_log"
       return $rc
    fi
  fi
  
  rm -f "$err_log"
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
  
  # Use find to list files, but logic is in loop
  while IFS= read -r java_file; do
    # Convert source path to class path
    class_file="$dest_dir/$(echo "$java_file" | sed "s|^$src_dir/||" | sed 's/\.java$/.class/')"
    
    if [ ! -f "$class_file" ] || [ "$java_file" -nt "$class_file" ]; then
       changed="$changed $java_file"
    fi
  done < <(find "$src_dir" -name "*.java" 2>/dev/null)
  
  echo "$changed"
}

# 1. Compile Main Sources
MAIN_SOURCES=$(get_changed_sources "src/main/java" "target/classes")
if [ -n "$MAIN_SOURCES" ]; then
    echo "Incremental compile: $(echo $MAIN_SOURCES | wc -w) main source files changed"
    compile_with_fallback target/classes "$CP" $MAIN_SOURCES
    rc=$?
    if [ $rc -ne 0 ]; then
        echo "Incremental compilation failed. Cleaning and retrying full compilation..."
        # If increment fails, we clean everything and rebuild ALL sources
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
    # echo "Main sources: no changes detected, skipping"
    :
fi

# 2. Compile EvoSuite Test Sources
EVOSUITE_SOURCES=$(get_changed_sources "evosuite-tests" "target/test-classes")
if [ -n "$EVOSUITE_SOURCES" ]; then
    # Test compilation needs target/test-classes in CP as well
    TEST_CP="$CP:target/test-classes"
    echo "Incremental compile: $(echo $EVOSUITE_SOURCES | wc -w) evosuite test files changed"
    compile_with_fallback target/test-classes "$TEST_CP" $EVOSUITE_SOURCES
fi

# 3. Compile Regular Test Sources (Optional / Commented out in original)
# TEST_SOURCES=$(get_changed_sources "src/test/java" "target/test-classes")
# if [ -n "$TEST_SOURCES" ]; then
#     compile_with_fallback target/test-classes "$CP:target/test-classes" $TEST_SOURCES
# fi

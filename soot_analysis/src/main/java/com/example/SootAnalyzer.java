package com.example;

import soot.*;
import soot.options.Options;
import soot.toolkits.graph.BriefUnitGraph;
import soot.toolkits.graph.UnitGraph;
import soot.toolkits.graph.pdg.HashMutablePDG;
import soot.toolkits.graph.pdg.PDGNode;
import soot.toolkits.graph.pdg.PDGRegion;
import soot.toolkits.graph.pdg.ProgramDependenceGraph;
import soot.toolkits.graph.pdg.IRegion;
import soot.toolkits.scalar.SmartLocalDefs;
import soot.toolkits.graph.SimpleDominatorsFinder;
import java.util.stream.Collectors;
import soot.toolkits.scalar.SimpleLiveLocals;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.*;

public class SootAnalyzer {

    public static void main(String[] args) {
        if (args.length < 3) {
            System.err.println("Usage: java -jar analyzer.jar <process_dir> <class_name> <method_signature> [output_file]");
            System.exit(1);
        }

        String processDir = args[0];
        String className = args[1];
        String methodName = args[2];
        String outputFile = args.length > 3 ? args[3] : "soot_output.json";

        setupSoot(processDir);

        try {
            SootClass sc = Scene.v().loadClassAndSupport(className);
            sc.setApplicationClass();
            Scene.v().loadNecessaryClasses();

            SootMethod method;
            try {
                method = sc.getMethod(methodName);
            } catch (RuntimeException e) {
                System.err.println("Method " + methodName + " not found in class " + className);
                System.err.println("Available methods:");
                for (SootMethod m : sc.getMethods()) {
                    System.err.println("  " + m.getName() + " " + m.getSignature());
                }
                throw e;
            }
            Body body = method.retrieveActiveBody();

            JSONObject analysisResult = analyzeMethod(method, body);

            try (FileWriter file = new FileWriter(outputFile)) {
                file.write(analysisResult.toString(2));
                System.out.println("Analysis result saved to " + outputFile);
            }

        } catch (Exception e) {
            System.err.println("Error during analysis: " + e.getMessage());
            e.printStackTrace();
            System.exit(1);
        }
    }

    private static void setupSoot(String processDir) {
        G.reset();
        Options.v().set_prepend_classpath(true);
        Options.v().set_allow_phantom_refs(true);
        Options.v().set_keep_line_number(true);
        Options.v().set_whole_program(true); // Needed for some interprocedural stuff, but might be slow.
        // For method-local analysis, whole_program might not be strictly needed but good for hierarchy.
        // Options.v().set_whole_program(false); 
        
        Options.v().set_process_dir(Collections.singletonList(processDir));
        Options.v().set_src_prec(Options.src_prec_class);
        Options.v().set_output_format(Options.output_format_none);
        
        // Add basic JDK to classpath if needed, Soot usually finds it.
        // On some systems specifically pointing to rt.jar or jrt-fs.jar is needed.
        // But with Java 9+ module system, Soot handles it if JAVA_HOME is set.
    }

    private static JSONObject analyzeMethod(SootMethod method, Body body) {
        UnitGraph cfg = new BriefUnitGraph(body);
        SmartLocalDefs localDefs = new SmartLocalDefs(cfg, new SimpleLiveLocals(cfg));

        JSONObject result = new JSONObject();
        result.put("method_name", method.getName());
        result.put("class_name", method.getDeclaringClass().getName());
        
        JSONArray unitsJson = new JSONArray();
        result.put("units", unitsJson);

        Map<Unit, Integer> unitIds = new HashMap<>();
        int idCounter = 0;
        for (Unit u : body.getUnits()) {
            unitIds.put(u, idCounter++);
        }

        for (Unit u : body.getUnits()) {
            JSONObject unitObj = new JSONObject();
            unitObj.put("id", unitIds.get(u));
            unitObj.put("content", u.toString());
            unitObj.put("line", u.getJavaSourceStartLineNumber());

            // 1. Identify Used Variables (UseBoxes)
            JSONArray usesJson = new JSONArray();
            for (ValueBox vb : u.getUseBoxes()) {
                Value val = vb.getValue();
                usesJson.put(analyzeValue(val));
            }
            unitObj.put("uses", usesJson);

            // 2. Identify Defined Variables (DefBoxes)
            JSONArray defsJson = new JSONArray();
            for (ValueBox vb : u.getDefBoxes()) {
                 Value val = vb.getValue();
                 defsJson.put(analyzeValue(val));
            }
            unitObj.put("defs", defsJson);
            
            // 3. Control Flow Successors
            JSONArray successorsJson = new JSONArray();
            for (Unit succ : cfg.getSuccsOf(u)) {
                successorsJson.put(unitIds.get(succ));
            }
            unitObj.put("successors", successorsJson);
            
            // 4. Branch Info (if specific)
            if (cfg.getSuccsOf(u).size() > 1) {
                unitObj.put("is_branch", true);
                if (u instanceof soot.jimple.IfStmt) {
                    soot.jimple.IfStmt ifStmt = (soot.jimple.IfStmt) u;
                    unitObj.put("branch_condition", ifStmt.getCondition().toString());
                    unitObj.put("branch_target", unitIds.get(ifStmt.getTarget()));
                } else if (u instanceof soot.jimple.LookupSwitchStmt) {
                    soot.jimple.LookupSwitchStmt switchStmt = (soot.jimple.LookupSwitchStmt) u;
                    unitObj.put("branch_key", switchStmt.getKey().toString());
                    unitObj.put("branch_default", unitIds.get(switchStmt.getDefaultTarget()));
                    JSONArray targets = new JSONArray();
                    for (int i = 0; i < switchStmt.getTargetCount(); i++) {
                        JSONObject t = new JSONObject();
                        t.put("lookup", switchStmt.getLookupValue(i));
                        t.put("target", unitIds.get(switchStmt.getTarget(i)));
                        targets.put(t);
                    }
                    unitObj.put("switch_targets", targets);
                } else if (u instanceof soot.jimple.TableSwitchStmt) {
                    soot.jimple.TableSwitchStmt switchStmt = (soot.jimple.TableSwitchStmt) u;
                    unitObj.put("branch_key", switchStmt.getKey().toString());
                    unitObj.put("branch_default", unitIds.get(switchStmt.getDefaultTarget()));
                    JSONArray targets = new JSONArray();
                    for (int i = 0; i < (switchStmt.getHighIndex() - switchStmt.getLowIndex() + 1); i++) {
                        JSONObject t = new JSONObject();
                        t.put("index", switchStmt.getLowIndex() + i);
                        t.put("target", unitIds.get(switchStmt.getTarget(i)));
                        targets.put(t);
                    }
                    unitObj.put("switch_targets", targets);
                }
            }

            unitsJson.put(unitObj);
        }
        
        // parameter analysis
        JSONArray paramsJson = new JSONArray();
        for( int i=0; i<method.getParameterCount(); i++) {
             JSONObject paramObj = new JSONObject();
             paramObj.put("index", i);
             paramObj.put("type", method.getParameterType(i).toString());
             // In Jimple, parameters are assigned to locals at the start: r0 := @this; r1 := @parameter0;
             // We can find the local variable name for each parameter.
             Local paramLocal = getLocalForParameter(body, i);
             if(paramLocal != null) {
                 paramObj.put("local_name", paramLocal.getName());
             }
             paramsJson.put(paramObj);
        }
        result.put("parameters", paramsJson);

        // --- PATH ANALYSIS ---
        try {
            int branchCount = 0;
            for (Unit u : body.getUnits()) {
                if (cfg.getSuccsOf(u).size() > 1) branchCount++;
            }
            // 简单的路径数估计：2 ^ 分支数（方法内）
            result.put("total_estimated_paths", Math.pow(2, Math.min(branchCount, 15))); 
        } catch (Exception e) {}

        // --- PDG DATA ---
        try {
            JSONArray nodesArray = new JSONArray();
            JSONArray edgesArray = new JSONArray();
            
            // Note: HashMutablePDG requires a CFG. BriefUnitGraph works.
            ProgramDependenceGraph pdg = new HashMutablePDG(cfg);
            
            // Map Soot units back to PDG nodes if possible
            // Iterate over all PDG nodes
            for (PDGNode node : pdg) {
                JSONObject nodeObj = new JSONObject();
                nodeObj.put("id", node.hashCode());
                nodeObj.put("type", node.getType().toString());
                
                JSONArray unitRefs = new JSONArray();
                Object nodeData = node.getNode();
                if (nodeData instanceof Unit) {
                    Unit uNode = (Unit) nodeData;
                    if (unitIds.containsKey(uNode)) {
                        unitRefs.put(unitIds.get(uNode));
                    }
                } else if (nodeData instanceof IRegion) {
                    IRegion region = (IRegion) nodeData;
                    for (Unit uReg : region.getUnits()) {
                        if (unitIds.containsKey(uReg)) {
                            unitRefs.put(unitIds.get(uReg));
                        }
                    }
                }
                nodeObj.put("units", unitRefs);
                nodesArray.put(nodeObj);
                
                // Edges (Dependences) - Using getDependents() for successors
                for (PDGNode succ : node.getDependents()) {
                    JSONObject edge = new JSONObject();
                    edge.put("from", node.hashCode());
                    edge.put("to", succ.hashCode());
                    edgesArray.put(edge);
                }
            }
            
            JSONObject pdgObj = new JSONObject();
            pdgObj.put("nodes", nodesArray);
            pdgObj.put("edges", edgesArray);
            result.put("pdg", pdgObj);
            
            // --- DOMINATOR ANALYSIS ---
            try {
                SimpleDominatorsFinder<Unit> doms = new SimpleDominatorsFinder<>(cfg);
                JSONArray dominatorsJson = new JSONArray();
                for (Unit u : body.getUnits()) {
                    List<Unit> ud = doms.getDominators(u);
                    JSONObject dObj = new JSONObject();
                    dObj.put("unit_id", unitIds.get(u));
                    dObj.put("dominator_ids", ud.stream().map(unitIds::get).collect(Collectors.toList()));
                    dominatorsJson.put(dObj);
                }
                result.put("dominators", dominatorsJson);
            } catch (Exception skip) {}
            
        } catch (Exception e) {
            System.err.println("PDG Generation Error: " + e.getMessage());
            // result.put("pdg_error", e.getMessage());
        }

        return result;
    }
    
    private static Local getLocalForParameter(Body body, int paramIndex) {
        // Simple heuristic: scan identity stmts at the beginning
        for(Unit u : body.getUnits()) {
             if (u instanceof soot.jimple.IdentityStmt) {
                 soot.jimple.IdentityStmt is = (soot.jimple.IdentityStmt) u;
                 Value right = is.getRightOp();
                 if (right instanceof soot.jimple.ParameterRef) {
                     soot.jimple.ParameterRef pr = (soot.jimple.ParameterRef) right;
                     if (pr.getIndex() == paramIndex) {
                         Value left = is.getLeftOp();
                         if (left instanceof Local) {
                             return (Local) left;
                         }
                     }
                 }
             }
        }
        return null;
    }

    private static JSONObject analyzeValue(Value val) {
        JSONObject valObj = new JSONObject();
        valObj.put("string", val.toString());
        valObj.put("type", val.getType().toString());
        
        if (val instanceof Local) {
            valObj.put("kind", "local");
            valObj.put("name", ((Local) val).getName());
        } else if (val instanceof soot.jimple.FieldRef) {
            valObj.put("kind", "field");
            soot.jimple.FieldRef fr = (soot.jimple.FieldRef) val;
            valObj.put("name", fr.getField().getName());
            valObj.put("declaring_class", fr.getField().getDeclaringClass().getName());
            valObj.put("is_static", fr instanceof soot.jimple.StaticFieldRef);
        } else if (val instanceof soot.jimple.Constant) {
            valObj.put("kind", "constant");
        } else {
             valObj.put("kind", "other");
             valObj.put("class", val.getClass().getSimpleName());
        }
        
        return valObj;
    }
}
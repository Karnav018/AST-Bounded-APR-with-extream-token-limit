"""
Token Limit Benchmark — For IEEE Paper Evidence
Generates 8 Java code samples of increasing size, each with a known bug.
Runs the AST extractor on each, and outputs a results CSV for the paper.
"""
import os
import sys
import csv

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from ast_extractor import process_bug

TESTS = [
    # (test_name, buggy_line, description, java_code)
    
    # --- Test 1: Tiny Method (25 tokens) ---
    (
        "T1_TinyMethod",
        5,
        "Off-by-one in tiny 5-line method",
        """public class Test1 {
    public int maxIndex(int[] arr) {
        int max = 0;
        for (int i = 0; i < arr.length; i++) {
            if (arr[i] > arr[max]) max = i;
        }
        return max;
    }
}"""
    ),
    
    # --- Test 2: Small Method (50 tokens) ---
    (
        "T2_SmallLoop",
        7,
        "Wrong loop termination condition",
        """public class Test2 {
    public int sumArray(int[] arr) {
        int total = 0;
        int count = 0;
        boolean flag = true;
        String label = "sum";
        // BUG: should be i < arr.length
        for (int i = 0; i <= arr.length; i++) {
            total += arr[i];
            count++;
        }
        System.out.println(label + ":" + count);
        return total;
    }
}"""
    ),
    
    # --- Test 3: Medium Method with IfStatement (~80 tokens) ---
    (
        "T3_MediumIf",
        14,
        "Wrong boundary check condition (< vs <=)",
        """public class Test3 {
    public double calcRatio(double amount, double balance) {
        double fee = 5.0;
        double tax = 0.1;
        double riskFactor = 1.05;
        double adjustedAmount = amount * riskFactor;
        double totalCost = adjustedAmount + fee;
        String status = "PENDING";
        int retries = 0;
        boolean isActive = true;

        System.out.println("Processing with balance: " + balance);
        System.out.println("Amount: " + adjustedAmount);

        // BUG: should be balance <= 0 to prevent zero-division
        if (balance < 0) {
            System.out.println("Invalid balance.");
            return -1;
        } else {
            double ratio = totalCost / balance;
            System.out.println("Ratio: " + ratio);
            return ratio;
        }
    }
}"""
    ),
    
    # --- Test 4: Large Method with nested logic (~200 tokens) ---
    (
        "T4_LargeNested",
        20,
        "Incorrect null check inverted",
        """public class Test4 {
    public String processUser(String userId, String email, int age) {
        String result = "INIT";
        String region = "US";
        String tier = "standard";
        boolean isVerified = false;
        boolean isActive = true;
        int loginCount = 42;
        double balance = 250.00;
        double creditScore = 720.5;
        double riskScore = 0.3;
        int failedAttempts = 0;

        System.out.println("Processing user: " + userId);
        System.out.println("Email: " + email);
        System.out.println("Age: " + age);

        // BUG: should be userId != null
        if (userId == null) {
            isVerified = true;
            System.out.println("User verified.");
        } else {
            result = "INVALID_ID";
            return result;
        }

        if (age >= 18 && creditScore > 700) {
            tier = "premium";
            balance += 100;
            System.out.println("Upgraded to premium tier.");
        } else if (age < 18) {
            result = "AGE_RESTRICTED";
            return result;
        }

        if (isVerified && isActive) {
            result = "SUCCESS";
        }

        System.out.println("Final Result: " + result);
        System.out.println("Tier: " + tier + " | Balance: " + balance);
        return result;
    }
}"""
    ),
    
    # --- Test 5: TryCatch Bug (~100 tokens) ---
    (
        "T5_TryCatch",
        10,
        "Exception caught but not rethrown or handled properly",
        """public class Test5 {
    public int parseAndDouble(String input) {
        int result = 0;
        String tag = "parser";
        boolean success = false;
        double factor = 2.0;
        int fallback = -1;

        // BUG: NumberFormatException swallowed silently, returns 0 instead of fallback
        try {
            result = Integer.parseInt(input);
            result *= factor;
            success = true;
        } catch (NumberFormatException e) {
            // Missing: result = fallback;
            System.out.println(tag + ": parse failed for input=" + input);
        }

        System.out.println("Parsed result: " + result);
        System.out.println("Success: " + success);
        return result;
    }
}"""
    ),
    
    # --- Test 6: WhileLoop Bug (~60 tokens) ---
    (
        "T6_WhileLoop",
        8,
        "Wrong while condition causes off-by-one",
        """public class Test6 {
    public int countDown(int start) {
        int count = 0;
        int step = 1;
        int total = start;
        double factor = 1.0;
        String log = "step";

        // BUG: should be total > 0, not total >= 0 (runs one extra iteration)
        while (total >= 0) {
            count++;
            total -= step;
            System.out.println(log + " count=" + count + " remaining=" + total);
        }

        return count;
    }
}"""
    ),
    
    # --- Test 7: ForLoop Wrong Initializer Bug (~45 tokens) ---
    (
        "T7_ForInit",
        5,
        "Loop starts at 1 instead of 0, misses first element",
        """public class Test7 {
    public int findMin(int[] arr) {
        int min = arr[0];
        String label = "min";
        // BUG: should start at 0 - misses comparing arr[0] against itself
        for (int i = 1; i < arr.length; i++) {
            if (arr[i] < min) min = arr[i];
        }
        System.out.println(label + "=" + min);
        return min;
    }
}"""
    ),
    
    # --- Test 8: String Comparison Bug (~30 tokens) ---
    (
        "T8_StringEquals",
        6,
        "== used instead of .equals() for String comparison",
        """public class Test8 {
    public boolean checkCode(String code) {
        String expected = "SECRET_007";
        boolean valid = false;
        int attempts = 1;
        // BUG: should use .equals() not ==
        if (code == expected) {
            valid = true;
        }
        System.out.println("Valid: " + valid + " (attempts: " + attempts + ")");
        return valid;
    }
}"""
    ),
]

def run_benchmark():
    results = []
    tmp_file = "temp_benchmark_test.java"
    
    print("\n" + "="*60)
    print("  TOKEN LIMIT BENCHMARK SUITE — Paper Evidence Collection")
    print("="*60 + "\n")

    for test_id, (name, buggy_line, description, java_code) in enumerate(TESTS, 1):
        with open(tmp_file, "w") as f:
            f.write(java_code)
        
        total_doc_tokens = len(java_code.split())
        res = process_bug(tmp_file, buggy_line)
        
        if res:
            status = "✅ WITHIN LIMIT" if res['ast_tokens'] <= 60 else "⚠️  OVER LIMIT"
            print(f"[Test {test_id}] {name}")
            print(f"  Bug: {description}")
            print(f"  Original Tokens : {total_doc_tokens}")
            print(f"  AST Tokens      : {res['ast_tokens']}")
            print(f"  Reduction       : {res['reduction_percent']}%")
            print(f"  Anchor Type     : {res['anchor_type']}")
            print(f"  Status          : {status}")
            print()
            
            results.append({
                "TestID": f"T{test_id}",
                "Name": name,
                "BugType": description,
                "OriginalDocTokens": total_doc_tokens,
                "ASTContextTokens": res['ast_tokens'],
                "ReductionPercent": res['reduction_percent'],
                "AnchorType": res['anchor_type'],
                "WithinBudget": res['ast_tokens'] <= 60
            })

    # Save results
    output_file = "benchmark_results.csv"
    with open(output_file, "w", newline="") as csvfile:
        fieldnames = ["TestID", "Name", "BugType", "OriginalDocTokens", "ASTContextTokens", "ReductionPercent", "AnchorType", "WithinBudget"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    # Summary
    within_budget = sum(1 for r in results if r["WithinBudget"])
    avg_ast_tokens = sum(r["ASTContextTokens"] for r in results) / len(results)
    avg_reduction = sum(r["ReductionPercent"] for r in results) / len(results)

    print("="*60)
    print(f"  SUMMARY:")
    print(f"  Total Tests           : {len(results)}")
    print(f"  Within 60-token limit : {within_budget}/{len(results)} ({round(within_budget/len(results)*100)}%)")
    print(f"  Average AST Tokens    : {round(avg_ast_tokens, 1)}")
    print(f"  Average Reduction     : {round(avg_reduction, 2)}%")
    print("="*60)
    print(f"\n  Results saved to: {output_file}")
    
    # Cleanup
    if os.path.exists(tmp_file):
        os.remove(tmp_file)

if __name__ == "__main__":
    run_benchmark()

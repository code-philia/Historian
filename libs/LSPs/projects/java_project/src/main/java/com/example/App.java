package com.example;

import com.example.utils.MathUtils;
import com.example.utils.StringUtils;

public class App {
    public static void main(String[] args) {
        // Use MathUtils and StringUtils
        int sum = MathUtils.add(5, 7)
        
        System.out.println("Sum: " + sum);

        String reversed = StringUtils.reverse("hello");
        System.out.println("Reversed: " + reversed);
    }
}

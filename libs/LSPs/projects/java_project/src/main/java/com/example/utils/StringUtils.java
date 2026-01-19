package com.example.utils;

public class StringUtils {
    // Reverse string
    public static String reverse(String input) {
        if (input == null) {
            return "";
        }
        return new StringBuilder(input).reverse().toString();
    }
}
